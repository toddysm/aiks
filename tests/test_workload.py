"""Context, values, and cluster ownership guards."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from aiks.config import LocalKubernetes, ReadinessWorkload, load_environment_config
from aiks.outputs import FoundationOutputs
from aiks.process import CommandResult
from aiks.workload import WorkloadRuntime, chart_path, conditions_ready, gateway_services

CONFIG = (
    Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config/dev.example.yaml"
)


@pytest.mark.parametrize(
    "url",
    [
        "http://index.example.invalid",
        "https://user:example@index.example.invalid",
        "https://index.example.invalid?key=example",
    ],
)
def test_package_index_rejects_credentials_and_insecure_transport(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS index without credentials"):
        ReadinessWorkload.validate_package_index(url)


def test_kind_node_image_must_be_digest_pinned() -> None:
    with pytest.raises(ValueError):
        LocalKubernetes(node_image="kindest/node:latest")


def test_local_values_and_chart() -> None:
    runtime = WorkloadRuntime(load_environment_config(CONFIG), "kind")
    values = runtime.values("aiks-readiness:content123")
    assert values["monitoring"]["enabled"] is False
    assert values["gateway"]["className"] == "eg"
    assert "identity" not in values
    with chart_path() as chart:
        assert (chart / "Chart.yaml").is_file()


def test_explicit_target_guards(tmp_path: Path) -> None:
    config = load_environment_config(CONFIG)
    with pytest.raises(ValueError, match="explicit"):
        WorkloadRuntime(config, "aks")
    with pytest.raises(ValueError, match="private managed"):
        WorkloadRuntime(config, "kind", kubeconfig=tmp_path / "other")


def test_stale_gateway_conditions() -> None:
    assert not conditions_ready({}, [{"type": "Accepted", "status": "True"}], {"Accepted"})
    resource = {"metadata": {"generation": 2}}
    assert not conditions_ready(
        resource, [{"type": "Accepted", "status": "True", "observedGeneration": 1}], {"Accepted"}
    )
    assert conditions_ready(
        resource, [{"type": "Accepted", "status": "True", "observedGeneration": 2}], {"Accepted"}
    )


def test_unowned_cluster_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = WorkloadRuntime(load_environment_config(CONFIG), "kind")
    runtime._private_directory()
    (runtime.directory / "owner.json").write_text(
        json.dumps({"owner": "other", "name": runtime.name})
    )
    with pytest.raises(ValueError, match="ownership"):
        runtime._verify_target()


@pytest.mark.parametrize("environment", ["kind", "aks-dev", "aks-production"])
def test_chart_target_security(environment: str) -> None:
    if not shutil.which("helm"):
        if os.environ.get("AIKS_REQUIRE_HELM") == "1":
            pytest.fail("Helm is required")
        pytest.skip("Helm chart tests run in workload CI")
    with chart_path() as chart:
        command = [
            "helm",
            "template",
            "aiks-readiness",
            str(chart),
            "-f",
            str(chart / f"values-{environment}.yaml"),
        ]
        if environment != "kind":
            command += [
                "--set",
                "image.repository=test.azurecr.io/readiness",
                "--set",
                "image.digest=sha256:" + "a" * 64,
                "--set",
                "identity.clientId=11111111-1111-4111-8111-111111111111",
                "--set",
                "identity.tenantId=11111111-1111-4111-8111-111111111111",
                "--set",
                "identity.vaultUri=https://example.vault.azure.net",
            ]
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
        if environment != "kind":
            rejected = subprocess.run(
                [*command, "--set", "identity.vaultUri=https://attacker.example.invalid"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert rejected.returncode != 0 and "vaultUri" in rejected.stderr
    docs = [entry for entry in yaml.safe_load_all(result.stdout) if entry]
    deployment = next(entry for entry in docs if entry["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"]
    assert not pod["automountServiceAccountToken"]
    assert "nodeSelector" not in pod and "tolerations" not in pod
    assert pod["containers"][0]["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert all(
        name in pod["containers"][0]
        for name in ("resources", "livenessProbe", "readinessProbe", "startupProbe")
    )
    kinds = {entry["kind"] for entry in docs}
    assert ("ServiceMonitor" in kinds) == (environment == "aks-production")
    assert ("GatewayClass" in kinds) == (environment == "kind")
    gateway = next(entry for entry in docs if entry["kind"] == "Gateway")
    if environment == "aks-production":
        assert (
            gateway["spec"]["infrastructure"]["annotations"][
                "service.beta.kubernetes.io/azure-load-balancer-internal"
            ]
            == "true"
        )
    if environment != "kind":
        assert "@sha256:" in pod["containers"][0]["image"]
        assert (
            deployment["spec"]["template"]["metadata"]["labels"]["azure.workload.identity/use"]
            == "true"
        )


class Tools:
    def __init__(self, runtime: WorkloadRuntime) -> None:
        self.runtime = runtime
        self.calls: list[tuple[str, ...]] = []
        self.exists = False
        self.uid = "test-cluster-uid"
        self.version = "v4.2.4"
        self.stale = False
        self.address = "10.96.1.2"
        self.probe_status = 200

    def run(self, arguments: tuple[str, ...], **kwargs: object) -> CommandResult:
        command = tuple(arguments)
        self.calls.append(command)
        result: object = ""
        if command[:3] == ("kind", "get", "clusters"):
            result = self.runtime.name if self.exists else ""
        elif command[:3] == ("kind", "create", "cluster"):
            self.exists = True
            self.runtime.kubeconfig.write_text("test-only")
        elif command[:3] == ("kind", "delete", "cluster"):
            self.exists = False
        elif command[:3] == ("helm", "version", "--short"):
            result = self.version
        elif command[:2] == ("helm", "list"):
            assert ("--all" in command) == self.version.startswith("v3.")
            result = []
        elif command[:3] == ("docker", "image", "inspect"):
            result = "sha256:" + "a" * 64
        elif command[:3] == ("az", "acr", "repository"):
            result = "sha256:" + "b" * 64
        elif command[0] == "kubectl":
            assert "--context" in command and "--kubeconfig" in command
            if "get" in command:
                kind = command[command.index("get") + 1]
                if kind == "services":
                    assert "--all-namespaces" in command and "-l" not in command
                    internal_annotation = "service.beta.kubernetes.io/azure-load-balancer-internal"
                    result = {
                        "items": [
                            {
                                "metadata": {
                                    "namespace": "controller-system",
                                    "annotations": {internal_annotation: "true"},
                                },
                                "spec": {"type": "LoadBalancer", "ports": [{"port": 80}]},
                                "status": {"loadBalancer": {"ingress": [{"ip": self.address}]}},
                            }
                        ]
                    }
                    return CommandResult(command, 0, json.dumps(result), "")
                conditions = [
                    {"type": name, "status": "True", "observedGeneration": 0 if self.stale else 1}
                    for name in ("Accepted", "Programmed", "ResolvedRefs")
                ]
                result = {
                    "metadata": {"generation": 1, "uid": self.uid},
                    "spec": {"controllerName": "test-controller"},
                    "status": {
                        "conditions": conditions,
                        "addresses": [{"value": self.address}],
                        "parents": [
                            {
                                "parentRef": {"name": "readiness"},
                                "controllerName": "test-controller",
                                "conditions": conditions,
                            }
                        ],
                    },
                }
                assert kind in {"namespace", "gatewayclass", "gateway", "httproute"}
            elif "view" in command:
                result = "https://aks.example.invalid"
            elif "exec" in command:
                result = self.probe(command[-2], command[-1])
        return CommandResult(
            command, 0, result if isinstance(result, str) else json.dumps(result), ""
        )

    def probe(self, address: str, path: str) -> dict[str, object]:
        body = (
            'aiks_readiness_info{status="ready"} 1'
            if path == "/metrics"
            else json.dumps({"status": "skipped" if self.runtime.target == "kind" else "verified"})
        )
        return {"status": self.probe_status, "body": body}


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[WorkloadRuntime, Tools]:
    monkeypatch.chdir(tmp_path)
    runtime = WorkloadRuntime(load_environment_config(CONFIG), "kind")
    tools = Tools(runtime)
    monkeypatch.setattr("aiks.workload.run_command", tools.run)
    return runtime, tools


@pytest.mark.parametrize("version", ["v3.19.0", "v4.2.4"])
def test_kind_lifecycle(runtime: tuple[WorkloadRuntime, Tools], version: str) -> None:
    service, tools = runtime
    tools.version = version
    assert service.build()["image"] == "aiks-readiness:local"
    service.create_local()
    service.create_local()
    assert service.install()["ready"]
    assert service.install(upgrade=True)["metrics"]
    assert service.rollback()["identity"] == "skipped"
    assert service.uninstall()["uninstalled"]
    assert service.delete_local()["deleted"]
    assert not service.directory.exists()
    assert not any(command[0] == "az" for command in tools.calls)


def test_replaced_cluster_and_bad_health_refused(runtime: tuple[WorkloadRuntime, Tools]) -> None:
    service, tools = runtime
    service.create_local()
    tools.uid = "replacement"
    with pytest.raises(ValueError, match="replaced"):
        service.delete_local()
    tools.uid = "test-cluster-uid"
    tools.stale = True
    with pytest.raises(ValueError, match="stale"):
        service.verify()
    tools.stale = False
    tools.probe_status = 503
    with pytest.raises(ValueError, match="probe failed"):
        service.verify()


def test_tool_failure_and_workspace_symlink(
    runtime: tuple[WorkloadRuntime, Tools], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _tools = runtime
    monkeypatch.setattr(
        "aiks.workload.run_command",
        lambda *args, **kwargs: CommandResult(("kind",), 1, "", "failed"),
    )
    with pytest.raises(ValueError, match="failed"):
        service._run("kind", "get", "clusters")
    service.directory.parent.mkdir(parents=True)
    service.directory.symlink_to(service.directory.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        service._private_directory()


def test_invalid_image_and_revision_refused(
    runtime: tuple[WorkloadRuntime, Tools], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _tools = runtime
    service.create_local()
    with pytest.raises(ValueError, match="revision"):
        service.rollback(-1)
    monkeypatch.setattr(service, "_run", lambda *args: "not-an-image-id")
    with pytest.raises(ValueError, match="image identity"):
        service._image()


def test_frontend_resolution_uses_status_and_ignores_unrelated_services() -> None:
    gateway = {"metadata": {"uid": "gateway-uid"}, "status": {"addresses": [{"value": "10.1.2.3"}]}}
    service = {
        "metadata": {"namespace": "controller-system"},
        "spec": {"type": "LoadBalancer", "ports": [{"port": 80}]},
        "status": {"loadBalancer": {"ingress": [{"ip": "10.1.2.3"}]}},
    }
    unrelated = {
        "spec": {"type": "LoadBalancer", "ports": [{"port": 80}]},
        "status": {"loadBalancer": {"ingress": [{"ip": "8.8.8.8"}]}},
    }
    assert gateway_services(gateway, [unrelated, service]) == [service]
    with pytest.raises(ValueError, match="could not be verified"):
        gateway_services(gateway, [unrelated])
    gateway["status"]["addresses"].append({"value": "10.1.2.4"})
    with pytest.raises(ValueError, match="could not be verified"):
        gateway_services(gateway, [service])


def foundation(environment: str = "dev") -> FoundationOutputs:
    group = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/test"
    identity = {
        "name": "identity",
        "id": group + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/test",
        "clientId": "11111111-1111-4111-8111-111111111111",
        "principalId": "11111111-1111-4111-8111-111111111111",
    }
    return FoundationOutputs.model_validate(
        {
            "environment": environment,
            "location": "westus3",
            "resourceGroup": {"name": "test", "id": group},
            "cluster": {
                "name": "aks",
                "id": group + "/providers/Microsoft.ContainerService/managedClusters/aks",
                "fqdn": "aks.example.invalid",
            },
            "registry": {
                "name": "test",
                "id": group + "/providers/Microsoft.ContainerRegistry/registries/test",
                "loginServer": "test.azurecr.io",
            },
            "vault": {
                "name": "vault",
                "id": group + "/providers/Microsoft.KeyVault/vaults/vault",
                "uri": "https://vault.vault.azure.net",
                "markerKeyName": "readiness-marker",
            },
            "network": {
                "vnetId": "network",
                "subnetIds": {
                    "apiServer": "api",
                    "systemNode": "system",
                    "userNode": "user",
                    "privateEndpoint": "private",
                },
            },
            "identities": {"cluster": identity, "readiness": identity},
            "monitoring": dict.fromkeys(
                (
                    "logAnalyticsId",
                    "azureMonitorWorkspaceId",
                    "prometheusQueryEndpoint",
                    "grafanaId",
                    "grafanaEndpoint",
                ),
                "",
            ),
            "readiness": {
                "namespace": "aiks-readiness",
                "serviceAccount": "readiness",
                "clientId": identity["clientId"],
                "tenantId": identity["clientId"],
                "vaultUri": "https://vault.vault.azure.net",
                "markerKeyName": "readiness-marker",
                "gatewayClassName": "approuting-istio",
                "internalGateway": environment == "production",
                "managedPrometheus": environment == "production",
            },
        }
    )


def test_aks_image_flow_and_public_gateway_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_environment_config(CONFIG.parent / "production.example.yaml")
    service = WorkloadRuntime(
        config,
        "aks",
        kubeconfig=tmp_path / "config",
        context="aks",
        outputs=foundation("production"),
    )
    tools = Tools(service)
    monkeypatch.setattr("aiks.workload.run_command", tools.run)
    monkeypatch.setattr(service, "_probe", tools.probe)
    image = service._image()
    assert image == "test.azurecr.io/readiness@sha256:" + "b" * 64
    assert service.values(image)["gateway"]["internal"]
    assert service.verify()["identity"] == "verified"
    tools.address = "8.8.8.8"
    with pytest.raises(ValueError, match="public address"):
        service.verify()
    config.spec.workload.image = image
    assert service._image() == image
    config.spec.workload.image = "other.azurecr.io/readiness@sha256:" + "b" * 64
    with pytest.raises(ValueError, match="configured registry"):
        service._image()
