"""Operator-invoked infrastructure lifecycle with explicit ownership and private artifacts."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Literal
from uuid import uuid4

from aiks.azure import AzureSession, object_response, require_owned_group
from aiks.config import EnvironmentConfig
from aiks.engines import bicep, terraform
from aiks.observability import TelemetryPendingError, verify_observability
from aiks.outputs import FoundationOutputs
from aiks.posture import verify_foundation, verify_roles
from aiks.preflight import check_tools, cloud_preflight, probe_host
from aiks.process import run_command
from aiks.workload import WorkloadRuntime

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]


class InfrastructureRuntime:
    def __init__(
        self,
        config: EnvironmentConfig,
        engine: Literal["bicep", "terraform"] | None,
        *,
        directory: Path | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.versions = check_tools(engine or "")
        self.azure = AzureSession()
        self.owner = terraform.owner(config)
        identity = f"{self.azure.subscription}/{self.owner}"
        self.directory = (directory or Path.cwd() / ".aiks/infra") / hashlib.sha256(
            identity.encode()
        ).hexdigest()[:16]
        self.deployment = f"aiks-{self.owner}"
        self.phase = "initializing"
        self.instance: str | None = None
        if self.engine is None:
            groups = self._groups()
            selected = groups[0].get("tags", {}).get("aiks-engine") if len(groups) == 1 else None
            if selected not in {"bicep", "terraform"}:
                raise ValueError("cannot discover the owning engine for verification")
            self.engine = selected
            self.versions = check_tools(selected)

    @contextmanager
    def _directory_handle(self, path: Path) -> Iterator[int]:
        parts = path.absolute().parts
        descriptor = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
        try:
            for index, part in enumerate(parts[1:]):
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
                if index >= len(parts) - 4:
                    os.fchmod(descriptor, 0o700)
            yield descriptor
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("infrastructure workspace must not use symbolic links") from error
            raise
        finally:
            os.close(descriptor)

    @contextmanager
    def session(self) -> Iterator[None]:
        if fcntl is None:
            raise ValueError("infrastructure operations require POSIX file locking")
        original_directory = self.directory
        absolute_directory = original_directory.absolute()
        previous = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self._directory_handle(absolute_directory) as directory:
                descriptor = os.open(
                    ".operation.lock",
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "a") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as error:
                        raise ValueError(
                            "another operation holds this environment workspace"
                        ) from error
                    os.fchdir(directory)
                    self.directory = Path(".")
                    if not os.path.samestat(
                        os.stat(absolute_directory, follow_symlinks=False), os.fstat(directory)
                    ):
                        raise ValueError(
                            "infrastructure workspace directory changed during acquisition"
                        )
                    if any(path.is_symlink() for path in self.directory.rglob("*")):
                        raise ValueError("infrastructure workspace contains symbolic links")
                    yield
                    if not os.path.samestat(
                        os.stat(absolute_directory, follow_symlinks=False), os.fstat(directory)
                    ):
                        raise ValueError(
                            "infrastructure workspace directory changed during operation"
                        )
        finally:
            self.directory = original_directory
            os.fchdir(previous)
            os.close(previous)

    def _run(self, *arguments: str, timeout: float | None = None) -> str:
        result = run_command(
            arguments,
            environment=self.azure.environment,
            timeout_seconds=timeout or self.config.spec.lifecycle.deployment_timeout_seconds,
        )
        if not result.succeeded:
            raise ValueError(f"{self.phase}: {arguments[0]} failed (exit {result.return_code})")
        return result.stdout

    @contextmanager
    def _temporary_artifact(self, prefix: str) -> Iterator[tuple[Path, int]]:
        with ExitStack() as stack:
            previous = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, previous)
            workspace = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, workspace)
            with tempfile.TemporaryDirectory(prefix=prefix + "-", dir=self.directory) as name:
                directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fchdir(directory)
                    path = Path("data")
                    descriptor = os.open(
                        path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600
                    )
                    os.close(descriptor)
                    yield path, workspace
                finally:
                    os.fchdir(previous)
                    os.close(directory)

    def _regular_file(self, path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("credential output must be a private regular file")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def _ownership_artifact(self, name: str) -> dict[str, Any]:
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
        finally:
            os.close(directory)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("ownership artifact must be a private regular file")
            text = handle.read(64 * 1024 + 1)
            if len(text) > 64 * 1024:
                raise ValueError("ownership artifact exceeds the size bound")
            return object_response(json.loads(text), "ownership artifact")

    def _groups(self) -> list[dict[str, Any]]:
        response = self.azure.json("group", "list")
        if not isinstance(response, list) or not all(
            isinstance(group, dict) and isinstance(group.get("name"), str) for group in response
        ):
            raise ValueError("resource-group inventory is unverifiable")
        return [
            group
            for group in response
            if group["name"].lower().startswith(f"rg-{self.owner}-".lower())
        ]

    def _owned_group(self, *, required: bool = True) -> dict[str, Any] | None:
        groups = self._groups()
        if len(groups) != 1:
            if not groups and not required:
                return None
            raise ValueError("expected exactly one owned environment group")
        group = groups[0]
        require_owned_group(group, self.config, self.azure.subscription)
        if (
            group["tags"].get("aiks-engine") != self.engine
            or group["tags"].get("aiks-owner") != self.owner
        ):
            raise ValueError("refusing to adopt an environment owned by another engine or workflow")
        return group

    def _engine_config(self) -> EnvironmentConfig:
        if self.instance is None:
            group = self._owned_group(required=False)
            if group is not None:
                self.instance = group["tags"].get("aiks-instance")
                if not self.instance:
                    raise ValueError("existing environment lacks a verified lifecycle instance tag")
            else:
                self.instance = str(uuid4())
        tags = {
            **self.config.spec.tags,
            "aiks-owner": self.owner,
            "aiks-engine": self.engine,
            "aiks-instance": self.instance,
        }
        return self.config.model_copy(
            update={"spec": self.config.spec.model_copy(update={"tags": tags})}
        )

    def _bicep(self, operation: Literal["validate", "what-if", "create"]) -> Any:
        parameters = self.directory / "parameters.json"
        terraform.write_json(parameters, bicep.parameters(self._engine_config()))
        compiled = self.directory / "template.json"
        with bicep.template_path() as template:
            if operation == "validate":
                self._run("bicep", "build", str(template), "--outfile", str(compiled))
                compiled.chmod(0o600)
        if not compiled.is_file():
            raise ValueError("compile and validate the Bicep template before execution")
        command = bicep.deployment_command(
            operation,
            config=self.config,
            subscription_id=self.azure.subscription,
            template=compiled,
            parameter_file=parameters,
        )
        return json.loads(self._run(*command))

    def _terraform(self, *arguments: str) -> str:
        try:
            return self._run(
                "terraform", f"-chdir={self.directory / 'terraform/environment'}", *arguments
            )
        finally:
            for path in self.directory.rglob("*"):
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and (path.suffix in {".json", ".tfplan"} or ".tfstate" in path.name)
                ):
                    path.chmod(0o600)

    def _prepare_terraform(self) -> None:
        destination = self.directory / "terraform"
        with terraform.asset_root() as source:
            allowed = {
                path.relative_to(source)
                for path in source.rglob("*.tf")
                if ".terraform" not in path.parts
            }
            shutil.copytree(
                source,
                destination,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".terraform", "*.tfstate*", "*.tfplan", "tests"),
            )
        unexpected = [
            path
            for path in destination.rglob("*")
            if ".terraform" not in path.parts
            and path.is_file()
            and (path.suffix == ".tf" or path.name.endswith(".tf.json"))
            and path.relative_to(destination) not in allowed
        ]
        if unexpected:
            raise ValueError("unexpected Terraform source in the private execution workspace")
        parameters = destination / "environment/inputs.tfvars.json"
        terraform.write_json(parameters, terraform.variables(self._engine_config()))
        terraform.write_json(
            destination / "environment/backend.json", terraform.backend(self.config)
        )
        self._terraform(
            "init",
            "-input=false",
            "-lockfile=readonly",
            "-reconfigure",
            "-backend-config=backend.json",
            "-no-color",
        )
        self._terraform("validate", "-json")

    def _preflight(self) -> dict[str, Any]:
        self.phase = "preflight"
        self._owned_group(required=False)
        report = cloud_preflight(self.config, self.azure)
        if self.engine == "terraform":
            from aiks.state import StateBackend

            backend = StateBackend(self.config, environment=self.azure.environment)
            if backend.subscription != self.azure.subscription:
                raise ValueError("Azure context changed during backend verification")
            backend.status()
        return {**report, "tools": self.versions, "engine": self.engine}

    def preflight(self) -> dict[str, Any]:
        with self.session():
            return self._preflight()

    def _outputs(self) -> FoundationOutputs:
        if self.engine == "bicep":
            deployment = self.azure.json("deployment", "sub", "show", "--name", self.deployment)
            value = deployment["properties"]["outputs"]["result"]["value"]
        else:
            value = json.loads(self._terraform("output", "-json", "result"))
        try:
            outputs = FoundationOutputs.model_validate(value)
        except ValueError as error:
            raise ValueError("infrastructure outputs do not satisfy the shared contract") from error
        group = self._owned_group()
        if (
            group is None
            or outputs.resource_group.id.lower() != group["id"].lower()
            or outputs.resource_group.name != group["name"]
            or outputs.environment != self.config.spec.environment
            or outputs.location != self.config.spec.location
        ):
            raise ValueError("outputs do not match the active subscription and owned environment")
        prefix = group["id"].lower() + "/providers/"
        identifiers = [
            outputs.cluster.id,
            outputs.registry.id,
            outputs.vault.id,
            outputs.network.vnet_id,
            outputs.identities.cluster.id,
            outputs.identities.readiness.id,
            *outputs.network.subnet_ids.model_dump().values(),
            outputs.monitoring.log_analytics_id,
            outputs.monitoring.azure_monitor_workspace_id,
            outputs.monitoring.grafana_id,
        ]
        if any(
            identifier and not identifier.lower().startswith(prefix) for identifier in identifiers
        ):
            raise ValueError("output resource reference escapes the owned environment")
        for model, kind in (
            (outputs.cluster, "Microsoft.ContainerService/managedClusters"),
            (outputs.registry, "Microsoft.ContainerRegistry/registries"),
            (outputs.vault, "Microsoft.KeyVault/vaults"),
            (outputs.identities.cluster, "Microsoft.ManagedIdentity/userAssignedIdentities"),
            (outputs.identities.readiness, "Microsoft.ManagedIdentity/userAssignedIdentities"),
        ):
            if (
                not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", model.name)
                or model.id.lower()
                != (group["id"] + "/providers/" + kind + "/" + model.name).lower()
            ):
                raise ValueError("output resource name/type does not match its identifier")
        if any(
            identifier
            and (
                any(part in {".", ".."} for part in identifier.split("/"))
                or any(character in identifier for character in "?#%\\")
            )
            for identifier in identifiers
        ):
            raise ValueError("output resource identifier contains unsafe path components")
        if (
            outputs.vault.marker_key_name != "readiness-marker"
            or outputs.readiness.marker_key_name != "readiness-marker"
            or outputs.readiness.vault_uri != outputs.vault.uri
        ):
            raise ValueError("output readiness marker or vault reference does not match")
        if (
            outputs.readiness.tenant_id.lower() != self.azure.tenant
            or outputs.readiness.client_id != outputs.identities.readiness.client_id
        ):
            raise ValueError("output identity context differs from the active environment")
        return outputs

    def plan(self) -> dict[str, Any]:
        with self.session():
            self._preflight()
            return self._plan()

    def _plan(self) -> dict[str, Any]:
        self.phase = "planning"
        self._owned_group(required=False)
        if self.engine == "bicep":
            self._bicep("validate")
            preview = object_response(self._bicep("what-if"), "Bicep preview")
            changes = preview.get("changes")
            if not isinstance(changes, list):
                raise ValueError("Bicep preview did not return verifiable changes")
            if any(
                not isinstance(change, dict)
                or change.get("changeType")
                not in {"NoChange", "Ignore", "Create", "Modify", "Delete", "Unsupported", "Deploy"}
                for change in changes
            ):
                raise ValueError("Bicep preview contains malformed changes")
            change_types = [
                change["changeType"]
                for change in changes
                if change["changeType"] not in {"NoChange", "Ignore"}
            ]
            if any(kind in {"Delete", "Unsupported", "Deploy"} for kind in change_types):
                raise ValueError("preview contains destructive or unverifiable changes")
            for change in changes:
                if change["changeType"] == "Modify":
                    self._check_bicep_delta(change.get("delta"))
        else:
            self._prepare_terraform()
            self._terraform(
                "plan",
                "-input=false",
                "-var-file=inputs.tfvars.json",
                "-out=environment.tfplan",
                "-no-color",
            )
            preview = object_response(
                json.loads(self._terraform("show", "-json", "environment.tfplan")),
                "Terraform preview",
            )
            self._check_terraform_plan(preview)
            changes = preview.get("resource_changes", [])
            change_types = [
                action
                for change in changes
                for action in change["change"]["actions"]
                if action not in {"no-op", "read"}
            ]
        terraform.write_json(self.directory / "preview.json", preview)
        return {
            "engine": self.engine,
            "environment": self.config.spec.environment,
            "changes": len(change_types),
            "noOp": not change_types,
        }

    def _check_terraform_plan(self, plan: dict[str, Any], *, destroy: bool = False) -> None:
        plan = object_response(plan, "Terraform plan")
        resources = plan.get("resource_changes")
        if not isinstance(resources, list):
            raise ValueError("Terraform resource changes must be a collection")
        group = self._owned_group(required=destroy)
        for resource in resources:
            resource = object_response(resource, "Terraform resource change")
            change = object_response(resource.get("change"), "Terraform change")
            actions = change.get("actions")
            if (
                not isinstance(actions, list)
                or not actions
                or any(
                    not isinstance(action, str)
                    or action not in {"no-op", "read", "create", "update", "delete"}
                    for action in actions
                )
            ):
                raise ValueError("Terraform change actions are malformed or unsupported")
            if destroy and any(action not in {"no-op", "read", "delete"} for action in actions):
                raise ValueError("destroy plan contains non-cleanup actions")
            if resource.get("mode") == "data":
                continue
            if not destroy and "delete" in actions:
                raise ValueError(
                    "deployment plan would replace or delete resources; review migration separately"
                )
            required_states = set()
            if any(action in actions for action in ("delete", "update")):
                required_states.add("before")
            if any(action in actions for action in ("create", "update")):
                required_states.add("after")
            for state in ("before", "after"):
                values = change.get(state)
                if state in required_states and not isinstance(values, dict):
                    raise ValueError(f"Terraform change lacks required {state} resource values")
                if values is not None and not isinstance(values, dict):
                    raise ValueError("Terraform before/after resource values are malformed")
                identifier = values.get("id") if isinstance(values, dict) else None
                if identifier is not None and not isinstance(identifier, str):
                    raise ValueError("Terraform resource identifier is malformed")
                if state == "before" and state in required_states and not identifier:
                    raise ValueError("Terraform prior resource identifier is unverifiable")
                if identifier and (
                    group is None
                    or not (
                        identifier.lower() == group["id"].lower()
                        or identifier.lower().startswith(group["id"].lower() + "/")
                    )
                ):
                    raise ValueError(
                        "Terraform plan references a resource outside the owned environment"
                    )

    def _check_bicep_delta(self, deltas: Any) -> None:
        if not isinstance(deltas, list) or not deltas:
            raise ValueError("modified Bicep resource lacks verifiable property deltas")
        for delta in deltas:
            delta = object_response(delta, "Bicep property delta")
            kind, path = delta.get("propertyChangeType"), delta.get("path")
            if kind not in {"Create", "Modify", "NoEffect"} or not isinstance(path, str):
                raise ValueError("Bicep property removal/replacement requires separate review")
            if delta.get("children"):
                self._check_bicep_delta(delta["children"])
            elif kind == "Modify" and not (
                path.startswith("tags.") or path == "properties.retentionInDays"
            ):
                raise ValueError("Bicep property modification requires separate review")

    def _resource(self, resource_id: str, api_version: str) -> dict[str, Any]:
        value = self.azure.json(
            "rest",
            "--method",
            "get",
            "--url",
            f"https://management.azure.com{resource_id}?api-version={api_version}",
        )
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("id"), str)
            or value["id"].lower() != resource_id.lower()
        ):
            raise ValueError("Azure resource identity could not be verified")
        return value

    def _observed(self, outputs: FoundationOutputs) -> dict[str, Any]:
        resource = self._resource
        observed = {
            "cluster": resource(outputs.cluster.id, "2026-04-01"),
            "registry": resource(outputs.registry.id, "2026-03-01-preview"),
            "vault": resource(outputs.vault.id, "2024-11-01"),
            "vnet": resource(outputs.network.vnet_id, "2025-01-01"),
            "clusterIdentity": resource(outputs.identities.cluster.id, "2024-11-30"),
            "readinessIdentity": resource(outputs.identities.readiness.id, "2024-11-30"),
            "federation": resource(
                outputs.identities.readiness.id + "/federatedIdentityCredentials/readiness",
                "2024-11-30",
            ),
            "marker": resource(
                outputs.vault.id + "/keys/" + outputs.vault.marker_key_name, "2024-11-01"
            ),
            "subnets": {
                name: resource(identifier, "2025-01-01")
                for name, identifier in (
                    ("api", outputs.network.subnet_ids.api_server),
                    ("system", outputs.network.subnet_ids.system_node),
                    ("user", outputs.network.subnet_ids.user_node),
                    ("private", outputs.network.subnet_ids.private_endpoint),
                )
            },
        }
        if self.config.spec.network.private_cluster:
            zone = (
                outputs.resource_group.id + "/providers/Microsoft.Network/privateDnsZones/"
                f"private.{self.config.spec.location}.azmk8s.io"
            )
            observed["privateDnsZone"] = resource(zone, "2024-06-01")
            link = zone + "/virtualNetworkLinks/" + outputs.network.vnet_id.rsplit("/", 1)[-1]
            observed["privateDnsLink"] = resource(link, "2024-06-01")
        return observed

    def _credentials(self, outputs: FoundationOutputs) -> WorkloadRuntime:
        path = self.directory / "kubeconfig"
        with self._temporary_artifact("credentials") as (temporary, workspace):
            self._run(
                "az",
                "aks",
                "get-credentials",
                "--resource-group",
                outputs.resource_group.name,
                "--name",
                outputs.cluster.name,
                "--file",
                str(temporary),
                "--overwrite-existing",
                "--format",
                "exec",
                "--subscription",
                self.azure.subscription,
                "--only-show-errors",
            )
            self._regular_file(temporary)
            self._run(
                "kubelogin",
                "convert-kubeconfig",
                "--login",
                "azurecli",
                "--kubeconfig",
                str(temporary),
            )
            self._regular_file(temporary)
            os.replace(temporary, "kubeconfig", dst_dir_fd=workspace)
        runtime = WorkloadRuntime(
            self.config, "aks", kubeconfig=path, context=outputs.cluster.name, outputs=outputs
        )
        runtime.directory = self.directory / "workload"
        runtime.environment = self.azure.environment
        return runtime

    def _verify(self, outputs: FoundationOutputs, *, install: bool = False) -> dict[str, Any]:
        self.phase = "resource-verification"
        observed = self._observed(outputs)
        report = verify_foundation(self.config, outputs, observed)
        private = self.config.spec.environment == "production"
        from urllib.parse import urlsplit

        for host, private_endpoint in (
            (outputs.cluster.fqdn, self.config.spec.network.private_cluster),
            (outputs.registry.login_server, private),
            (urlsplit(outputs.vault.uri).hostname, private),
        ):
            if not host:
                raise ValueError("resource endpoint is missing")
            probe_host(
                host,
                private=private_endpoint,
                timeout=self.config.spec.lifecycle.connection_timeout_seconds,
            )
        self._verify_roles(outputs, observed)
        self.phase = "workload-verification"
        runtime = self._credentials(outputs)
        started = perf_counter()
        workload = runtime.install() if install else runtime.verify()
        duration = perf_counter() - started
        if duration > self.config.spec.lifecycle.verification_timeout_seconds:
            raise ValueError("readiness verification exceeded the configured time bound")
        self.phase = "monitoring-verification"
        monitoring = self._monitoring(outputs)
        self._private_frontends(outputs, observed, runtime, workload)
        snapshot = self._snapshot(outputs)
        return {
            **report,
            "roles": "verified",
            "readiness": "verified",
            "readinessSeconds": round(duration, 3),
            "monitoring": monitoring,
            "inventory": snapshot,
        }

    def _monitoring(self, outputs: FoundationOutputs) -> dict[str, str]:
        deadline = perf_counter() + self.config.spec.lifecycle.verification_timeout_seconds
        while True:
            try:
                return verify_observability(self.config, outputs, self.azure, self._resource)
            except TelemetryPendingError:
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    raise
                sleep(min(self.config.spec.lifecycle.poll_interval_seconds, remaining))

    def _wait_alert(self, outputs: FoundationOutputs, condition: str, since: datetime) -> None:
        from urllib.parse import urlencode

        query = urlencode(
            {
                "api-version": "2019-03-01",
                "targetResource": outputs.monitoring.azure_monitor_workspace_id,
                "monitorCondition": condition,
            }
        )
        url = f"https://management.azure.com/subscriptions/{self.azure.subscription}/providers/Microsoft.AlertsManagement/alerts?{query}"
        deadline = perf_counter() + self.config.spec.lifecycle.alert_timeout_seconds
        while True:
            response = object_response(
                self.azure.json("rest", "--method", "get", "--url", url), "alert evidence"
            )
            if response.get("nextLink"):
                raise ValueError("alert evidence is paginated and cannot be verified completely")
            entries = response.get("value")
            if not isinstance(entries, list):
                raise ValueError("alert evidence response is missing its collection")
            for alert in entries:
                properties = object_response(
                    object_response(alert, "alert").get("properties"), "alert properties"
                )
                essentials = object_response(properties.get("essentials"), "alert essentials")
                rule = essentials.get("alertRule")
                target = essentials.get("targetResource")
                if not isinstance(rule, str) or not isinstance(target, str):
                    raise ValueError("alert identity metadata is malformed")
                if (
                    "ReadinessUnavailable" not in rule
                    or essentials.get("monitorCondition") != condition
                ):
                    continue
                if target.lower() != outputs.monitoring.azure_monitor_workspace_id.lower():
                    continue
                try:
                    changed = datetime.fromisoformat(
                        essentials["lastModifiedDateTime"].replace("Z", "+00:00")
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if changed.tzinfo and changed >= since:
                    return
            remaining = deadline - perf_counter()
            if remaining <= 0:
                raise ValueError(f"fresh readiness alert {condition.lower()} evidence timed out")
            sleep(min(self.config.spec.lifecycle.poll_interval_seconds, remaining))

    def exercise_alerts(
        self, *, confirmed_environment: str, allow_production: bool = False
    ) -> dict[str, Any]:
        self._confirm(confirmed_environment, allow_production)
        if not self.config.spec.observability.managed_prometheus:
            raise ValueError("alert drill requires managed Prometheus and notification actions")
        with self.session():
            self._owned_group()
            if self.engine == "terraform":
                self._prepare_terraform()
            outputs = self._outputs()
            runtime = self._credentials(outputs)
            runtime.verify()
            deployment = runtime._get("deployment", "readiness")
            replicas = deployment.get("spec", {}).get("replicas")
            if not isinstance(replicas, int) or replicas < 1:
                raise ValueError("readiness replicas cannot be safely restored")
            started = datetime.now(UTC)
            self.phase = "alert-fire"
            try:
                runtime._kubectl(
                    "scale", "deployment/readiness", "-n", "aiks-readiness", "--replicas=0"
                )
                self._wait_alert(outputs, "Fired", started)
            finally:
                runtime._kubectl(
                    "scale",
                    "deployment/readiness",
                    "-n",
                    "aiks-readiness",
                    f"--replicas={replicas}",
                )
                runtime.verify()
            self.phase = "alert-resolve"
            self._wait_alert(outputs, "Resolved", started)
            return {
                "alertFired": True,
                "alertResolved": True,
                "replicasRestored": True,
                "notificationDelivery": "operator-confirmation-required",
            }

    def _private_frontends(
        self,
        outputs: FoundationOutputs,
        observed: dict[str, Any],
        runtime: WorkloadRuntime,
        workload: dict[str, Any],
    ) -> None:
        if self.config.spec.environment != "production":
            return
        from urllib.parse import urlsplit

        base = outputs.resource_group.name.removeprefix("rg-")
        for service, prefix, target, host in (
            ("registry", "acr", outputs.registry.id, outputs.registry.login_server),
            ("vault", "vault", outputs.vault.id, urlsplit(outputs.vault.uri).hostname),
        ):
            endpoint = self._resource(
                outputs.resource_group.id
                + f"/providers/Microsoft.Network/privateEndpoints/pe-{prefix}-{base}",
                "2025-01-01",
            )
            endpoint_properties = object_response(endpoint.get("properties"), "private endpoint")
            subnet_id = object_response(
                endpoint_properties.get("subnet"), "private endpoint subnet"
            ).get("id")
            if (
                not isinstance(subnet_id, str)
                or subnet_id.lower() != outputs.network.subnet_ids.private_endpoint.lower()
            ):
                raise ValueError(f"{service} private endpoint subnet binding drift")
            connections = endpoint["properties"].get("privateLinkServiceConnections", [])
            if (
                len(connections) != 1
                or connections[0]["properties"].get("privateLinkServiceId", "").lower()
                != target.lower()
                or connections[0]["properties"]
                .get("privateLinkServiceConnectionState", {})
                .get("status")
                != "Approved"
            ):
                raise ValueError(f"{service} private endpoint target/approval mismatch")
            zone_name = (
                "privatelink.azurecr.io"
                if service == "registry"
                else "privatelink.vaultcore.azure.net"
            )
            zone_id = (
                outputs.resource_group.id
                + "/providers/Microsoft.Network/privateDnsZones/"
                + zone_name
            )
            endpoint_id = (
                outputs.resource_group.id
                + f"/providers/Microsoft.Network/privateEndpoints/pe-{prefix}-{base}"
            )
            zone_group = self._resource(endpoint_id + "/privateDnsZoneGroups/default", "2025-01-01")
            configurations = zone_group["properties"].get("privateDnsZoneConfigs")
            if not isinstance(configurations, list) or len(configurations) != 1:
                raise ValueError(f"{service} private endpoint DNS-zone binding drift")
            zone_properties = object_response(
                object_response(configurations[0], "private DNS configuration").get("properties"),
                "private DNS configuration properties",
            )
            actual_zone = zone_properties.get("privateDnsZoneId")
            if not isinstance(actual_zone, str) or actual_zone.lower() != zone_id.lower():
                raise ValueError(f"{service} private endpoint DNS-zone binding drift")
            link = self._resource(
                zone_id + f"/virtualNetworkLinks/pe-{prefix}-{base}", "2024-06-01"
            )
            from aiks.posture import assert_properties

            assert_properties(
                link,
                {
                    "properties.virtualNetwork.id": outputs.network.vnet_id,
                    "properties.registrationEnabled": False,
                },
                f"{service} private DNS link",
            )
            addresses: set[str] = set()
            for reference in endpoint["properties"].get("networkInterfaces", []):
                interface = self._resource(reference["id"], "2025-01-01")
                addresses.update(
                    entry["properties"]["privateIPAddress"]
                    for entry in interface["properties"]["ipConfigurations"]
                )
            if (
                not host
                or not addresses
                or not set(
                    probe_host(
                        host,
                        private=True,
                        timeout=self.config.spec.lifecycle.connection_timeout_seconds,
                    )
                )
                <= addresses
            ):
                raise ValueError(f"{service} DNS does not target its private endpoint")
        api_addresses = probe_host(
            outputs.cluster.fqdn,
            private=True,
            timeout=self.config.spec.lifecycle.connection_timeout_seconds,
        )
        if any(
            ip_address(address) not in ip_network(self.config.spec.network.api_server_subnet_cidr)
            for address in api_addresses
        ):
            raise ValueError("private cluster API resolves outside its configured API subnet")
        node_group = observed["cluster"]["properties"]["nodeResourceGroup"]
        load_balancers = self._collection(
            f"/subscriptions/{self.azure.subscription}/resourceGroups/{node_group}"
            "/providers/Microsoft.Network/loadBalancers",
            "2025-01-01",
        )
        for load_balancer in load_balancers:
            properties = load_balancer["properties"]
            ingress_ids = {
                rule["properties"]["frontendIPConfiguration"]["id"].lower()
                for rule in properties.get("loadBalancingRules", [])
                + properties.get("inboundNatRules", [])
            }
            outbound_ids = {
                reference["id"].lower()
                for rule in properties.get("outboundRules", [])
                for reference in rule["properties"].get("frontendIPConfigurations", [])
            }
            if any(
                frontend["properties"].get("publicIPAddress")
                and (
                    frontend.get("id", "").lower() in ingress_ids
                    or frontend.get("id", "").lower() not in outbound_ids
                )
                for frontend in properties.get("frontendIPConfigurations", [])
            ):
                raise ValueError("production load balancer exposes a public inbound frontend")
        frontends = [
            frontend["properties"]
            for load_balancer in load_balancers
            for frontend in load_balancer["properties"].get("frontendIPConfigurations", [])
        ]
        gateway = runtime._get("gateway", "readiness")
        gateway_addresses = {address["value"] for address in gateway["status"]["addresses"]}
        if workload["gatewayAddress"] not in gateway_addresses:
            raise ValueError("Gateway changed during verification")
        for address in gateway_addresses:
            matches = [
                frontend for frontend in frontends if frontend.get("privateIPAddress") == address
            ]
            if len(matches) != 1 or matches[0].get("publicIPAddress"):
                raise ValueError("production Gateway ARM frontend is unverified or public")

    def _snapshot(self, outputs: FoundationOutputs) -> dict[str, int]:
        query = (
            f"Resources | where resourceGroup =~ '{outputs.resource_group.name}' "
            "| project id, type, name, location"
        )
        snapshot = self.azure.json(
            "rest",
            "--method",
            "post",
            "--url",
            "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01",
            "--body",
            json.dumps(
                {
                    "subscriptions": [self.azure.subscription],
                    "query": query,
                    "options": {"$top": 1000, "resultFormat": "objectArray"},
                }
            ),
        )
        snapshot = object_response(snapshot, "resource graph inventory")
        if (
            snapshot.get("$skipToken")
            or snapshot.get("resultTruncated") in {True, "true"}
            or not isinstance(snapshot.get("data"), list)
        ):
            raise ValueError("resource graph inventory is incomplete")
        resources = snapshot["data"]
        if any(
            not isinstance(resource, dict)
            or not isinstance(resource.get("id"), str)
            or not isinstance(resource.get("type"), str)
            for resource in resources
        ):
            raise ValueError("resource graph inventory entries are malformed")
        group_id = outputs.resource_group.id.lower()
        if any(
            resource["id"].lower() != group_id
            and not resource["id"].lower().startswith(group_id + "/")
            for resource in resources
        ):
            raise ValueError("resource graph inventory includes a resource outside the environment")
        required = {
            outputs.cluster.id.lower(),
            outputs.registry.id.lower(),
            outputs.vault.id.lower(),
            outputs.network.vnet_id.lower(),
            outputs.identities.cluster.id.lower(),
            outputs.identities.readiness.id.lower(),
        }
        if not required <= {resource["id"].lower() for resource in resources}:
            raise ValueError("resource graph inventory has not verified all core resources")
        counts = dict(Counter(resource["type"].lower() for resource in resources))
        from aiks.preflight import foundation_policy

        allowed = {kind.lower() for kind in foundation_policy("parity/contract.json")["inventory"]}
        allowed.add("microsoft.network/networkinterfaces")
        if set(counts) - allowed:
            raise ValueError("unexpected resource type in the owned environment")
        core_counts = {
            "microsoft.containerservice/managedclusters": 1,
            "microsoft.containerregistry/registries": 1,
            "microsoft.keyvault/vaults": 1,
            "microsoft.network/virtualnetworks": 1,
            "microsoft.managedidentity/userassignedidentities": 2,
        }
        if any(counts.get(kind, 0) != count for kind, count in core_counts.items()):
            raise ValueError("unexpected core resource count in the owned environment")
        for kind, enabled in (
            (
                "microsoft.operationalinsights/workspaces",
                self.config.spec.observability.container_insights,
            ),
            ("microsoft.monitor/accounts", self.config.spec.observability.managed_prometheus),
            ("microsoft.dashboard/grafana", self.config.spec.observability.managed_grafana),
        ):
            if counts.get(kind, 0) != int(enabled):
                raise ValueError(
                    "unexpected or missing monitoring resource in environment inventory"
                )
        self._complete_inventory(outputs, counts, resources)
        terraform.write_json(self.directory / "inventory.json", counts)
        return counts

    def _collection(self, identifier: str, version: str) -> list[dict[str, Any]]:
        response = object_response(
            self.azure.json(
                "rest",
                "--method",
                "get",
                "--url",
                f"https://management.azure.com{identifier}?api-version={version}",
            ),
            "resource collection",
        )
        values = response.get("value")
        if (
            response.get("nextLink")
            or not isinstance(values, list)
            or any(not isinstance(value, dict) for value in values)
        ):
            raise ValueError("resource collection is incomplete or malformed")
        return values

    def _complete_inventory(
        self, outputs: FoundationOutputs, counts: dict[str, int], resources: list[dict[str, Any]]
    ) -> None:
        from aiks.preflight import foundation_policy

        settings = self.config.spec.observability
        production = self.config.spec.environment == "production"
        private = self.config.spec.network.private_cluster
        alerts = production or bool(
            settings.action_group_receivers or settings.action_group_resource_ids
        )
        expected = {
            kind.lower(): modes[self.config.spec.environment]
            for kind, modes in foundation_policy("parity/contract.json")["inventory"].items()
        }
        expected.update(
            {
                "microsoft.operationalinsights/workspaces": int(settings.container_insights),
                "microsoft.monitor/accounts": int(settings.managed_prometheus),
                "microsoft.dashboard/grafana": int(settings.managed_grafana),
                "microsoft.insights/datacollectionrules": int(settings.container_insights)
                + int(settings.managed_prometheus),
                "microsoft.insights/datacollectionruleassociations": int(
                    settings.container_insights
                )
                + int(settings.managed_prometheus),
                "microsoft.insights/diagnosticsettings": int(settings.container_insights),
                "microsoft.insights/actiongroups": int(bool(settings.action_group_receivers)),
                "microsoft.insights/activitylogalerts": int(alerts),
                "microsoft.alertsmanagement/prometheusrulegroups": int(
                    alerts and settings.managed_prometheus
                ),
                "microsoft.insights/scheduledqueryrules": int(
                    alerts and settings.container_insights
                ),
                "microsoft.network/privatednszones": int(private) + 2 * int(production),
                "microsoft.network/privatednszones/virtualnetworklinks": int(private)
                + 2 * int(production),
                "microsoft.network/privateendpoints": 2 * int(production),
                "microsoft.network/privateendpoints/privatednszonegroups": 2 * int(production),
                "microsoft.authorization/roleassignments": 5
                + int(private)
                + int(settings.managed_grafana)
                + int(settings.managed_grafana and settings.managed_prometheus),
            }
        )
        counts["microsoft.resources/resourcegroups"] = 1
        child_collections = [
            (
                "microsoft.network/virtualnetworks/subnets",
                outputs.network.vnet_id + "/subnets",
                "2025-01-01",
            ),
            (
                "microsoft.managedidentity/userassignedidentities/federatedidentitycredentials",
                outputs.identities.readiness.id + "/federatedIdentityCredentials",
                "2024-11-30",
            ),
            (
                "microsoft.managedidentity/userassignedidentities/federatedidentitycredentials",
                outputs.identities.cluster.id + "/federatedIdentityCredentials",
                "2024-11-30",
            ),
            ("microsoft.keyvault/vaults/keys", outputs.vault.id + "/keys", "2024-11-01"),
            (
                "microsoft.insights/datacollectionruleassociations",
                outputs.cluster.id + "/providers/Microsoft.Insights/dataCollectionRuleAssociations",
                "2023-03-11",
            ),
            (
                "microsoft.insights/diagnosticsettings",
                outputs.cluster.id + "/providers/Microsoft.Insights/diagnosticSettings",
                "2016-09-01",
            ),
        ]
        for kind, _identifier, _version in child_collections:
            counts[kind] = 0
        for kind, identifier, version in child_collections:
            counts[kind] += len(self._collection(identifier, version))
        assignments = self.azure.json("role", "assignment", "list", "--all")
        if not isinstance(assignments, list) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("scope"), str)
            for entry in assignments
        ):
            raise ValueError("role inventory is malformed")
        prefix = outputs.resource_group.id.lower()
        counts["microsoft.authorization/roleassignments"] = sum(
            entry["scope"].lower() == prefix or entry["scope"].lower().startswith(prefix + "/")
            for entry in assignments
        )
        counts["microsoft.network/privatednszones/virtualnetworklinks"] = 0
        counts["microsoft.network/privateendpoints/privatednszonegroups"] = 0
        expected_interfaces: set[str] = set()
        for resource in resources:
            kind = resource["type"].lower()
            if kind == "microsoft.network/privatednszones":
                counts["microsoft.network/privatednszones/virtualnetworklinks"] += len(
                    self._collection(resource["id"] + "/virtualNetworkLinks", "2024-06-01")
                )
            elif kind == "microsoft.network/privateendpoints":
                counts["microsoft.network/privateendpoints/privatednszonegroups"] += len(
                    self._collection(resource["id"] + "/privateDnsZoneGroups", "2025-01-01")
                )
                endpoint = self._resource(resource["id"], "2025-01-01")
                expected_interfaces.update(
                    entry["id"].lower()
                    for entry in endpoint["properties"].get("networkInterfaces", [])
                )
        actual_interfaces = {
            resource["id"].lower()
            for resource in resources
            if resource["type"].lower() == "microsoft.network/networkinterfaces"
        }
        if actual_interfaces != expected_interfaces:
            raise ValueError("unexplained or missing service-generated network interface")
        expected["microsoft.network/networkinterfaces"] = len(expected_interfaces)
        if any(counts.get(kind, 0) != count for kind, count in expected.items()):
            raise ValueError("complete live inventory differs from the shared parity contract")

    def _verify_roles(self, outputs: FoundationOutputs, observed: dict[str, Any]) -> None:
        from importlib.resources import files

        packaged = files("aiks").joinpath("infrastructure/aks-automatic/parity/contract.json")
        path = (
            packaged
            if packaged.is_file()
            else Path(__file__).resolve().parents[2]
            / "infrastructure/aks-automatic/parity/contract.json"
        )
        roles = json.loads(path.read_text())["roles"]
        expected = {
            (outputs.cluster.id, str(self.config.spec.identity.admin_group_object_id), roles[name])
            for name in (
                "Azure Kubernetes Service Cluster User Role",
                "Azure Kubernetes Service RBAC Cluster Admin",
            )
        }
        expected.update(
            {
                (
                    outputs.network.vnet_id,
                    outputs.identities.cluster.principal_id,
                    roles["Network Contributor"],
                ),
                (
                    outputs.registry.id,
                    observed["cluster"]["properties"]["identityProfile"]["kubeletidentity"][
                        "objectId"
                    ],
                    roles["AcrPull"],
                ),
                (
                    outputs.vault.id,
                    outputs.identities.readiness.principal_id,
                    roles["Key Vault Reader"],
                ),
            }
        )
        if self.config.spec.network.private_cluster:
            expected.add(
                (
                    observed["cluster"]["properties"]["apiServerAccessProfile"]["privateDNSZone"],
                    outputs.identities.cluster.principal_id,
                    roles["Private DNS Zone Contributor"],
                )
            )
        if self.config.spec.observability.managed_grafana:
            grafana = self._resource(outputs.monitoring.grafana_id, "2024-10-01")
            expected.add(
                (
                    outputs.monitoring.grafana_id,
                    str(self.config.spec.identity.admin_group_object_id),
                    roles["Grafana Admin"],
                )
            )
            if self.config.spec.observability.managed_prometheus:
                expected.add(
                    (
                        outputs.monitoring.azure_monitor_workspace_id,
                        grafana["identity"]["principalId"],
                        roles["Monitoring Reader"],
                    )
                )
        assignments = self.azure.json("role", "assignment", "list", "--all")
        if not isinstance(assignments, list) or any(
            not isinstance(assignment, dict)
            or not isinstance(assignment.get("scope"), str)
            or not assignment["scope"]
            for assignment in assignments
        ):
            raise ValueError("role inventory is unverifiable")
        prefix = outputs.resource_group.id.lower()
        local = [
            assignment
            for assignment in assignments
            if assignment.get("scope", "").lower() == prefix
            or assignment.get("scope", "").lower().startswith(prefix + "/")
        ]
        verify_roles(local, expected)

    def deploy(
        self, *, confirmed_environment: str, allow_production: bool = False
    ) -> dict[str, Any]:
        self._confirm(confirmed_environment, allow_production)
        with self.session():
            self._preflight()
            preview = self._plan()
            started = perf_counter()
            self.phase = "deployment"
            terraform.write_json(
                self.directory / "intent.json",
                {"engine": self.engine, "owner": self.owner, "instance": self.instance},
            )
            if self.engine == "bicep":
                self._bicep("create")
            else:
                self._terraform(
                    "apply", "-input=false", "-auto-approve", "-no-color", "environment.tfplan"
                )
            outputs = self._outputs()
            terraform.write_json(
                self.directory / "outputs.json", outputs.model_dump(mode="json", by_alias=True)
            )
            group = self._owned_group()
            instance = group.get("tags", {}).get("aiks-instance") if group else None
            if not instance or instance != self.instance:
                raise ValueError("deployed environment instance tag was not verified")
            terraform.write_json(
                self.directory / "owner.json",
                {"engine": self.engine, "groupId": outputs.resource_group.id, "instance": instance},
            )
            verified = self._verify(outputs, install=True)
            duration = perf_counter() - started
            if duration > self.config.spec.lifecycle.deployment_timeout_seconds:
                raise ValueError("deployment-to-verified exceeded the configured time bound")
            return {
                "engine": self.engine,
                "environment": self.config.spec.environment,
                "phase": "verified",
                "preview": preview,
                "verification": verified,
                "applyToVerifiedSeconds": round(duration, 3),
            }

    def verify(self) -> dict[str, Any]:
        with self.session():
            self._owned_group()
            if self.engine == "terraform":
                self._prepare_terraform()
            return self._verify(self._outputs())

    def _confirm(self, environment: str, allow_production: bool) -> None:
        if environment != self.config.spec.environment:
            raise ValueError("environment confirmation did not match")
        if environment == "production" and not allow_production:
            raise ValueError("production mutation requires an explicit override")

    def _remove_empty_environment_state(self) -> None:
        from aiks.state import StateBackend

        backend = StateBackend(self.config, environment=self.azure.environment)
        if backend.subscription != self.azure.subscription:
            raise ValueError("Azure context changed before environment-state cleanup")
        key = terraform.backend(self.config)["key"]
        inventory = backend.inventory()
        entry = next((blob for blob in inventory if blob.get("name") == key), None)
        if entry is None:
            raise ValueError("environment state key was not found after destruction")
        if terraform.blob_lease(entry)["status"] != "unlocked":
            raise ValueError("environment state has an active lease")
        lease = str(uuid4())
        deleted = False
        with tempfile.TemporaryFile(mode="w+b", dir=self.directory) as handle:
            try:
                backend._blob(
                    "lease",
                    "acquire",
                    "--blob-name",
                    key,
                    "--lease-duration",
                    "-1",
                    "--proposed-lease-id",
                    lease,
                )
                backend._blob(
                    "download",
                    "--name",
                    key,
                    "--file",
                    f"/dev/fd/{handle.fileno()}",
                    "--overwrite",
                    "true",
                    "--lease-id",
                    lease,
                    pass_fds=(handle.fileno(),),
                )
                if os.fstat(handle.fileno()).st_size > 32 * 1024 * 1024:
                    raise ValueError("environment state exceeds the recovery bound")
                handle.seek(0)
                state = json.loads(handle.read(32 * 1024 * 1024 + 1))
                terraform.check_empty_environment_state(state)
                if self._groups():
                    raise ValueError("environment appeared during state cleanup")
                backend._blob("delete", "--name", key, "--lease-id", lease)
                deleted = True
                if any(blob.get("name") == key for blob in backend.inventory()):
                    raise ValueError("environment state-key removal was not verified")
            finally:
                if not deleted:
                    backend._blob("lease", "release", "--blob-name", key, "--lease-id", lease)

    def destroy(
        self,
        *,
        confirmed_environment: str,
        allow_production: bool = False,
        allow_partial: bool = False,
    ) -> dict[str, Any]:
        self._confirm(confirmed_environment, allow_production)
        with self.session():
            self.phase = "cleanup-ownership"
            group = self._owned_group()
            if group is None:
                raise ValueError("owned environment could not be verified")
            if allow_partial:
                return self._destroy_partial(group, confirmed_environment, allow_production)
            receipt = self._ownership_artifact("owner.json")
            if (
                receipt.get("engine") != self.engine
                or receipt.get("groupId", "").lower() != group["id"].lower()
            ):
                raise ValueError("cleanup requires the matching deployment ownership receipt")
            if not receipt.get("instance") or receipt["instance"] != group["tags"].get(
                "aiks-instance"
            ):
                raise ValueError("environment was replaced; refusing cleanup")
            if self.engine == "terraform":
                self._prepare_terraform()
            outputs = self._outputs()
            self.phase = "workload-cleanup"
            self._snapshot(outputs)
            self._credentials(outputs).uninstall()
            current = self._owned_group()
            if current is None or current["tags"].get("aiks-instance") != receipt["instance"]:
                raise ValueError("environment ownership changed during cleanup")
            self.phase = "resource-cleanup"
            if self.engine == "bicep":
                self._run(
                    *bicep.destroy_command(
                        config=self.config,
                        subscription_id=self.azure.subscription,
                        resource_group_id=group["id"],
                        confirmed_environment=confirmed_environment,
                        allow_production=allow_production,
                    )
                )
            else:
                self._terraform(
                    "plan",
                    "-destroy",
                    "-input=false",
                    "-var-file=inputs.tfvars.json",
                    "-out=destroy.tfplan",
                    "-no-color",
                )
                self._check_terraform_plan(
                    json.loads(self._terraform("show", "-json", "destroy.tfplan")), destroy=True
                )
                self._terraform(
                    "apply", "-input=false", "-auto-approve", "-no-color", "destroy.tfplan"
                )
            if self.azure.json("group", "exists", "--name", group["name"]) is not False:
                raise ValueError("environment resource-group deletion was not verified")
            if self.engine == "terraform":
                self._remove_empty_environment_state()
            deleted_vaults = self.azure.json("keyvault", "list-deleted")
            retained = [
                vault for vault in deleted_vaults if vault.get("name") == outputs.vault.name
            ]
            result = {
                "environmentDeleted": True,
                "backendRetained": True,
                "softDeletedVaults": [
                    {
                        "name": outputs.vault.name,
                        "reason": "soft-delete retention; purge not attempted",
                        "purgeProtection": self.config.spec.environment == "production",
                    }
                ]
                if retained
                else [],
            }
            terraform.write_json(self.directory / "cleanup.json", result)
            return result

    def _destroy_partial(
        self, group: dict[str, Any], confirmation: str, allow_production: bool
    ) -> dict[str, Any]:
        intent = self._ownership_artifact("intent.json")
        if (
            intent.get("engine") != self.engine
            or intent.get("owner") != self.owner
            or not intent.get("instance")
            or intent["instance"] != group["tags"].get("aiks-instance")
        ):
            raise ValueError(
                "partial cleanup requires a matching deployment intent and live instance"
            )
        resources = self.azure.json("resource", "list", "--resource-group", group["name"])
        if not isinstance(resources, list):
            raise ValueError("partial resource inventory is unverifiable")
        for resource in resources:
            tags = resource.get("tags") or {}
            if (
                tags.get("aiks-instance") != intent["instance"]
                or tags.get("aiks-engine") != self.engine
                or tags.get("aiks-owner") != self.owner
            ):
                raise ValueError("partial cleanup found a resource without verifiable ownership")
        self.phase = "partial-resource-cleanup"
        if self.engine == "bicep":
            self._run(
                *bicep.destroy_command(
                    config=self.config,
                    subscription_id=self.azure.subscription,
                    resource_group_id=group["id"],
                    confirmed_environment=confirmation,
                    allow_production=allow_production,
                )
            )
        else:
            self._prepare_terraform()
            self._terraform(
                "plan",
                "-destroy",
                "-input=false",
                "-var-file=inputs.tfvars.json",
                "-out=destroy.tfplan",
                "-no-color",
            )
            self._check_terraform_plan(
                json.loads(self._terraform("show", "-json", "destroy.tfplan")), destroy=True
            )
            self._terraform("apply", "-input=false", "-auto-approve", "-no-color", "destroy.tfplan")
        if self.azure.json("group", "exists", "--name", group["name"]) is not False:
            raise ValueError("partial environment deletion was not verified")
        if self.engine == "terraform":
            self._remove_empty_environment_state()
        result = {
            "environmentDeleted": True,
            "partialCleanup": True,
            "backendRetained": True,
            "retentionReview": "inspect soft-deleted vaults and retained backend versions",
        }
        terraform.write_json(self.directory / "cleanup.json", result)
        return result
