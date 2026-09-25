"""Explicit-context Helm and isolated kind workload lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from importlib.resources import as_file, files
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from aiks.config import EnvironmentConfig
from aiks.engines.terraform import write_json
from aiks.outputs import FoundationOutputs
from aiks.process import run_command

NAMESPACE = "aiks-readiness"
RELEASE = "aiks-readiness"
RELEASE_NAMESPACE = "aiks-system"
CONTROLLER_NAMESPACE = "envoy-gateway-system"
CONTROLLER_CHART = "oci://docker.io/envoyproxy/gateway-helm"


@contextmanager
def chart_path() -> Iterator[Path]:
    packaged = files("aiks").joinpath("infrastructure/aks-automatic/charts/readiness")
    if packaged.is_dir():
        with as_file(packaged) as path:
            yield path
    else:
        path = Path(__file__).resolve().parents[2] / "infrastructure/aks-automatic/charts/readiness"
        if not (path / "Chart.yaml").is_file():
            raise FileNotFoundError("readiness chart is missing; reinstall aiks")
        yield path


def conditions_ready(
    resource: dict[str, Any], conditions: list[dict[str, Any]], wanted: set[str]
) -> bool:
    generation = resource.get("metadata", {}).get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        return False
    return all(
        any(
            condition.get("type") == name
            and condition.get("status") == "True"
            and condition.get("observedGeneration") == generation
            for condition in conditions
        )
        for name in wanted
    )


def gateway_services(
    gateway: dict[str, Any], services: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve frontends from controller-reported addresses, not naming conventions."""
    addresses = {
        str(ip_address(entry["value"])) for entry in gateway.get("status", {}).get("addresses", [])
    }
    uid = gateway.get("metadata", {}).get("uid")
    matches = []
    covered: set[str] = set()
    for service in services:
        spec = service.get("spec", {})
        if spec.get("type") != "LoadBalancer" or not any(
            port.get("port") == 80 for port in spec.get("ports", [])
        ):
            continue
        frontends = {
            str(ip_address(entry["ip"]))
            for entry in service.get("status", {}).get("loadBalancer", {}).get("ingress", [])
            if "ip" in entry
        }
        owned = bool(uid) and any(
            reference.get("uid") == uid
            for reference in service.get("metadata", {}).get("ownerReferences", [])
        )
        if owned or addresses.intersection(frontends):
            matches.append(service)
            covered.update(addresses.intersection(frontends))
    if not addresses or covered != addresses or not matches:
        raise ValueError("production Gateway service could not be verified")
    return matches


class WorkloadRuntime:
    def __init__(
        self,
        config: EnvironmentConfig,
        target: Literal["kind", "aks"],
        *,
        kubeconfig: Path | None = None,
        context: str | None = None,
        outputs: FoundationOutputs | None = None,
    ) -> None:
        self.config = config
        self.target = target
        self.outputs = outputs
        self.name = config.spec.local.kind_cluster_name
        self.timeout = config.spec.workload.timeout_seconds
        self.environment: dict[str, str] | None = None
        self.directory = Path.cwd() / ".aiks" / "local" / self.name
        self.kubeconfig = kubeconfig or self.directory / "kubeconfig"
        self.context = context or f"kind-{self.name}"
        self.owner = hashlib.sha256(
            f"{config.spec.naming.prefix}/{config.spec.environment}".encode()
        ).hexdigest()
        if target == "aks" and (kubeconfig is None or context is None or outputs is None):
            raise ValueError("AKS requires explicit --kubeconfig, --context, and --outputs")
        if target == "aks" and outputs is not None:
            if (
                outputs.environment != config.spec.environment
                or outputs.location != config.spec.location
            ):
                raise ValueError("foundation outputs do not match the configured environment")
            if (
                outputs.readiness.namespace != NAMESPACE
                or outputs.readiness.service_account != "readiness"
            ):
                raise ValueError("foundation readiness identity contract does not match the chart")
        if target == "kind" and (kubeconfig is not None or context is not None):
            raise ValueError("kind uses only its private managed kubeconfig and context")

    def _run(self, *arguments: str) -> str:
        result = run_command(
            arguments, timeout_seconds=self.timeout + 120, environment=self.environment
        )
        if not result.succeeded:
            raise ValueError(f"{arguments[0]} failed ({result.return_code}): {result.stderr}")
        return result.stdout

    def _kubectl(self, *arguments: str) -> str:
        return self._run(
            "kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context, *arguments
        )

    def _helm(self, *arguments: str) -> str:
        return self._run(
            "helm", *arguments, "--kubeconfig", str(self.kubeconfig), "--kube-context", self.context
        )

    def _get(self, kind: str, name: str, namespace: str | None = NAMESPACE) -> dict[str, Any]:
        arguments = ("-n", namespace) if namespace else ()
        value = json.loads(self._kubectl("get", kind, name, *arguments, "-o", "json"))
        if not isinstance(value, dict):
            raise ValueError("invalid Kubernetes resource response")
        return value

    def _private_directory(self) -> None:
        for directory in (self.directory.parent.parent, self.directory.parent, self.directory):
            if directory.is_symlink():
                raise ValueError("local workspace must not use symbolic links")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        if any(path.is_symlink() for path in self.directory.iterdir()):
            raise ValueError("local workspace contains symbolic links")

    def _verify_target(self) -> None:
        if self.target == "kind":
            self._private_directory()
            receipt = json.loads((self.directory / "owner.json").read_text())
            if receipt.get("owner") != self.owner or receipt.get("name") != self.name:
                raise ValueError("local cluster ownership does not match this configuration")
            current = self._get("namespace", "kube-system", None)["metadata"]["uid"]
            if current != receipt.get("uid"):
                raise ValueError("local cluster was replaced; refusing operation")
        else:
            if self.outputs is None:
                raise ValueError("AKS foundation outputs are required")
            server = self._kubectl(
                "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"
            ).strip()
            endpoint = urlsplit(server)
            if endpoint.scheme != "https" or endpoint.hostname != self.outputs.cluster.fqdn:
                raise ValueError(
                    "Kubernetes context does not match the foundation cluster endpoint"
                )

    def create_local(self) -> dict[str, Any]:
        self._private_directory()
        existing = self._run("kind", "get", "clusters").split()
        if self.name in existing:
            self._verify_target()
        else:
            self._run(
                "kind",
                "create",
                "cluster",
                "--name",
                self.name,
                "--image",
                self.config.spec.local.node_image,
                "--kubeconfig",
                str(self.kubeconfig),
                "--wait",
                f"{self.timeout}s",
            )
            self.kubeconfig.chmod(0o600)
            uid = self._get("namespace", "kube-system", None)["metadata"]["uid"]
            receipt = self.directory / "owner.json"
            write_json(receipt, {"name": self.name, "owner": self.owner, "uid": uid})
        self._helm(
            "upgrade",
            "--install",
            "eg",
            CONTROLLER_CHART,
            "--version",
            self.config.spec.local.gateway_chart_version,
            "--namespace",
            CONTROLLER_NAMESPACE,
            "--create-namespace",
            "--wait",
            "--timeout",
            f"{self.timeout}s",
        )
        self._kubectl(
            "wait",
            "--for=condition=Established",
            "crd/gateways.gateway.networking.k8s.io",
            f"--timeout={self.timeout}s",
        )
        return {
            "cluster": self.name,
            "controllerVersion": self.config.spec.local.gateway_chart_version,
        }

    def delete_local(self) -> dict[str, Any]:
        self._verify_target()
        self._run(
            "kind", "delete", "cluster", "--name", self.name, "--kubeconfig", str(self.kubeconfig)
        )
        if self.name in self._run("kind", "get", "clusters").split():
            raise ValueError("local cluster deletion was not verified")
        shutil.rmtree(self.directory)
        return {"cluster": self.name, "deleted": True}

    def build(self) -> dict[str, str]:
        source = Path(__file__).resolve().parents[2]
        dockerfile = source / "infrastructure/aks-automatic/readiness-app/Dockerfile"
        if not dockerfile.is_file():
            raise ValueError(
                "image build requires a source checkout; supply a prebuilt workload.image"
            )
        self._run(
            "docker",
            "build",
            "--build-arg",
            f"PIP_INDEX_URL={self.config.spec.workload.package_index_url}",
            "-f",
            str(dockerfile),
            "-t",
            self.config.spec.workload.image,
            str(source),
        )
        return {"image": self.config.spec.workload.image}

    def _image(self) -> str:
        image = self.config.spec.workload.image
        if self.target == "aks" and "@sha256:" in image:
            if self.outputs is None:
                raise ValueError("AKS foundation outputs are required")
            if not image.startswith(self.outputs.registry.login_server + "/") or not re.fullmatch(
                r".+@sha256:[a-f0-9]{64}", image
            ):
                raise ValueError("AKS image must be an immutable digest in the configured registry")
            return image
        image_id = self._run("docker", "image", "inspect", "--format", "{{.Id}}", image).strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise ValueError("could not verify the local image identity")
        tag = "readiness-" + image_id[7:23]
        if self.target == "kind":
            immutable_tag = f"aiks-readiness:{tag}"
            self._run("docker", "tag", image, immutable_tag)
            self._run("kind", "load", "docker-image", immutable_tag, "--name", self.name)
            return immutable_tag
        if self.outputs is None:
            raise ValueError("AKS foundation outputs are required")
        subscription = str(UUID(self.outputs.registry.id.split("/")[2]))
        remote = f"{self.outputs.registry.login_server}/readiness:{tag}"
        self._run(
            "az",
            "acr",
            "login",
            "--name",
            self.outputs.registry.name,
            "--subscription",
            subscription,
            "--only-show-errors",
        )
        self._run("docker", "tag", image, remote)
        self._run("docker", "push", remote)
        digest = self._run(
            "az",
            "acr",
            "repository",
            "show",
            "--name",
            self.outputs.registry.name,
            "--image",
            f"readiness:{tag}",
            "--query",
            "digest",
            "--output",
            "tsv",
            "--subscription",
            subscription,
            "--only-show-errors",
        ).strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError("registry did not return a valid image digest")
        return f"{self.outputs.registry.login_server}/readiness@{digest}"

    def values(self, image: str) -> dict[str, Any]:
        repository, separator, digest = image.partition("@")
        tag = "unused"
        if not separator:
            repository, tag = image.rsplit(":", 1)
        production = self.target == "aks" and self.config.spec.environment == "production"
        value: dict[str, Any] = {
            "target": self.target,
            "environment": self.config.spec.environment,
            "cluster": self.name
            if self.target == "kind"
            else self.outputs.cluster.name
            if self.outputs
            else "",
            "replicas": self.config.spec.workload.replicas or (2 if production else 1),
            "ready": self.config.spec.workload.ready,
            "image": {
                "repository": repository,
                "tag": tag,
                "digest": digest,
                "pullPolicy": "IfNotPresent",
            },
            "gateway": {
                "className": "eg" if self.target == "kind" else "approuting-istio",
                "internal": production,
            },
            "monitoring": {
                "enabled": self.target == "aks"
                and self.config.spec.observability.managed_prometheus
            },
            "podDisruptionBudget": {"enabled": production},
        }
        if self.target == "aks":
            if self.outputs is None:
                raise ValueError("AKS foundation outputs are required")
            value["identity"] = {
                "clientId": self.outputs.readiness.client_id,
                "tenantId": self.outputs.readiness.tenant_id,
                "vaultUri": self.outputs.vault.uri,
                "markerKeyName": self.outputs.vault.marker_key_name,
            }
        return value

    def install(self, *, upgrade: bool = False) -> dict[str, Any]:
        self._verify_target()
        image = self._image()
        self._private_directory()
        values_file = self.directory / f"{self.target}-values.json"
        write_json(values_file, self.values(image))
        major = self._run("helm", "version", "--short").lstrip("v").split(".")[0]
        rollback_flag = "--rollback-on-failure" if major == "4" else "--atomic"
        with chart_path() as chart:
            install = () if upgrade else ("--install",)
            self._helm(
                "upgrade",
                *install,
                RELEASE,
                str(chart),
                "--namespace",
                RELEASE_NAMESPACE,
                "--create-namespace",
                "--reset-values",
                "--values",
                str(values_file),
                "--wait",
                rollback_flag,
                "--timeout",
                f"{self.timeout}s",
            )
        return self.verify()

    def rollback(self, revision: int = 0) -> dict[str, Any]:
        self._verify_target()
        if revision < 0:
            raise ValueError("revision must not be negative")
        self._helm(
            "rollback",
            RELEASE,
            str(revision),
            "--namespace",
            RELEASE_NAMESPACE,
            "--wait",
            "--timeout",
            f"{self.timeout}s",
        )
        return self.verify()

    def uninstall(self) -> dict[str, Any]:
        self._verify_target()
        self._helm(
            "uninstall",
            RELEASE,
            "--ignore-not-found",
            "--namespace",
            RELEASE_NAMESPACE,
            "--wait",
            "--timeout",
            f"{self.timeout}s",
        )
        major = self._run("helm", "version", "--short").lstrip("v").split(".")[0]
        all_statuses = () if major == "4" else ("--all",)
        remaining = json.loads(
            self._helm(
                "list",
                "--namespace",
                RELEASE_NAMESPACE,
                *all_statuses,
                "--filter",
                f"^{RELEASE}$",
                "--output",
                "json",
            )
        )
        if remaining:
            raise ValueError("Helm release removal was not verified")
        return {"target": self.target, "uninstalled": True}

    def _probe(self, address: str, path: str) -> dict[str, Any]:
        address = str(ip_address(address))
        if self.target == "kind":
            script = (
                "import http.client,json,sys; "
                "connection=http.client.HTTPConnection(sys.argv[1],80,timeout=10); "
                "connection.request('GET',sys.argv[2]); response=connection.getresponse(); "
                "print(json.dumps({'status':response.status,'body':response.read(1000000).decode()}))"
            )
            response = json.loads(
                self._kubectl(
                    "exec",
                    "deployment/readiness",
                    "-n",
                    NAMESPACE,
                    "-c",
                    "readiness",
                    "--",
                    "python",
                    "-c",
                    script,
                    address,
                    path,
                )
            )
            return dict(response)
        connection = HTTPConnection(address, 80, timeout=10)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return {"status": response.status, "body": response.read(1000000).decode()}
        finally:
            connection.close()

    def verify(self) -> dict[str, Any]:
        self._verify_target()
        self._kubectl(
            "rollout",
            "status",
            "deployment/readiness",
            "-n",
            NAMESPACE,
            f"--timeout={self.timeout}s",
        )
        class_name = "eg" if self.target == "kind" else "approuting-istio"
        for resource, namespaced, condition in [
            (f"gatewayclass/{class_name}", False, "Accepted"),
            ("gateway/readiness", True, "Accepted"),
            ("gateway/readiness", True, "Programmed"),
        ]:
            namespace = ("-n", NAMESPACE) if namespaced else ()
            self._kubectl(
                "wait",
                f"--for=condition={condition}",
                resource,
                *namespace,
                f"--timeout={self.timeout}s",
            )
        for condition in ("Accepted", "ResolvedRefs"):
            self._kubectl(
                "wait",
                f'--for=jsonpath={{.status.parents[0].conditions[?(@.type=="{condition}")].status}}=True',
                "httproute/readiness",
                "-n",
                NAMESPACE,
                f"--timeout={self.timeout}s",
            )
        gateway_class = self._get("gatewayclass", class_name, None)
        gateway = self._get("gateway", "readiness")
        route = self._get("httproute", "readiness")
        if not conditions_ready(
            gateway_class, gateway_class.get("status", {}).get("conditions", []), {"Accepted"}
        ) or not conditions_ready(
            gateway, gateway.get("status", {}).get("conditions", []), {"Accepted", "Programmed"}
        ):
            raise ValueError("Gateway status is missing or stale")
        if not any(
            parent.get("parentRef", {}).get("name") == "readiness"
            and parent.get("parentRef", {}).get("namespace", NAMESPACE) == NAMESPACE
            and parent.get("controllerName") == gateway_class["spec"]["controllerName"]
            and conditions_ready(route, parent.get("conditions", []), {"Accepted", "ResolvedRefs"})
            for parent in route.get("status", {}).get("parents", [])
        ):
            raise ValueError("HTTPRoute references are missing, rejected, or stale")
        addresses = gateway.get("status", {}).get("addresses", [])
        if not addresses:
            raise ValueError("Gateway has no assigned address")
        address = str(ip_address(addresses[0]["value"]))
        if (
            self.target == "aks"
            and self.config.spec.environment == "production"
            and not all(
                any(
                    ip_address(entry["value"]) in ip_network(cidr)
                    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
                )
                for entry in addresses
            )
        ):
            raise ValueError("production Gateway exposes a public address")
        if self.target == "aks" and self.config.spec.environment == "production":
            inventory = json.loads(
                self._kubectl(
                    "get",
                    "services",
                    "--all-namespaces",
                    "-o",
                    "json",
                )
            ).get("items", [])
            services = gateway_services(gateway, inventory)
            for service in services:
                annotations = service.get("metadata", {}).get("annotations", {})
                frontend = service.get("status", {}).get("loadBalancer", {}).get("ingress", [])
                if (
                    service.get("spec", {}).get("type") != "LoadBalancer"
                    or annotations.get("service.beta.kubernetes.io/azure-load-balancer-internal")
                    != "true"
                    or not frontend
                    or any(
                        "ip" not in entry
                        or not any(
                            ip_address(entry["ip"]) in ip_network(cidr)
                            for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
                        )
                        for entry in frontend
                    )
                ):
                    raise ValueError(
                        "production Gateway service has an unverified or public frontend"
                    )
        ready = self._probe(address, "/readyz")
        identity = self._probe(address, "/identityz")
        metrics = self._probe(address, "/metrics")
        if any(response["status"] != 200 for response in (ready, identity, metrics)):
            raise ValueError("readiness route, identity, or metrics probe failed")
        if json.loads(identity["body"]).get("status") != (
            "skipped" if self.target == "kind" else "verified"
        ):
            raise ValueError("identity result does not match the target")
        if "aiks_readiness_info{" not in metrics["body"]:
            raise ValueError("readiness metric is missing")
        return {
            "target": self.target,
            "gatewayAddress": address,
            "ready": True,
            "identity": "skipped" if self.target == "kind" else "verified",
            "metrics": True,
        }
