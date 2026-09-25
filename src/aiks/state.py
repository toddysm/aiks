"""Operator-run Azure backend lifecycle; never invoked by offline validation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from time import sleep
from typing import Any
from uuid import UUID, uuid4

from aiks.config import EnvironmentConfig
from aiks.engines.terraform import (
    BOOTSTRAP_KEY,
    asset_root,
    backend,
    blob_lease,
    bootstrap_variables,
    check_blobs,
    check_recovery,
    owner,
    write_json,
)
from aiks.process import run_command

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)


class StateBackend:
    def __init__(
        self,
        config: EnvironmentConfig,
        *,
        directory: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.subscription = ""
        self.environment = {
            key: value
            for key, value in (environment if environment is not None else os.environ).items()
            if not key.startswith(
                (
                    "ARM_",
                    "TF_",
                    "AZURE_STORAGE_",
                    "AZURE_CLIENT_",
                    "AZURE_TENANT_",
                    "AZURE_FEDERATED_",
                    "AZURE_USERNAME",
                    "AZURE_PASSWORD",
                )
            )
        }
        self.environment.update(
            {
                "ARM_USE_CLI": "true",
                "ARM_USE_MSI": "false",
                "ARM_USE_OIDC": "false",
                "TF_IN_AUTOMATION": "1",
            }
        )
        account = self._az("account", "show")
        if not isinstance(account, dict) or account.get("state") != "Enabled":
            raise ValueError("an enabled Azure CLI account is required")
        self.subscription = str(UUID(account["id"]))
        self.environment["ARM_SUBSCRIPTION_ID"] = self.subscription
        self.environment["ARM_TENANT_ID"] = str(UUID(account["tenantId"]))
        state = config.spec.terraform
        identity = (
            f"{self.subscription}/{state.state_resource_group}/"
            f"{state.state_storage_account}/{state.state_container}"
        )
        self.directory = (directory or Path.cwd() / ".aiks" / "state") / hashlib.sha256(
            identity.encode()
        ).hexdigest()[:16]
        self.group_id = (
            f"/subscriptions/{self.subscription}/resourceGroups/{state.state_resource_group}"
        )
        self.account_id = (
            f"{self.group_id}/providers/Microsoft.Storage/storageAccounts/"
            f"{state.state_storage_account}"
        )

    def _run(self, *arguments: str) -> str:
        for attempt in range(4):
            result = run_command(arguments, timeout_seconds=1800, environment=self.environment)
            read_operation = arguments[:3] == ("az", "storage", "blob") and arguments[3] in {
                "list",
                "download",
                "show",
            }
            propagation_failure = any(
                code in result.stderr
                for code in ("AuthorizationPermissionMismatch", "RoleAssignmentNotFound")
            )
            if result.succeeded or not read_operation or not propagation_failure or attempt == 3:
                break
            sleep(2**attempt)
        if not result.succeeded:
            raise ValueError(f"{arguments[0]} failed ({result.return_code}): {result.stderr}")
        return result.stdout

    def _az(self, *arguments: str) -> Any:
        scope = ("--subscription", self.subscription) if self.subscription else ()
        output = self._run("az", *arguments, *scope, "--only-show-errors", "--output", "json")
        try:
            return json.loads(output) if output.strip() else None
        except json.JSONDecodeError as error:
            raise ValueError(
                "Azure returned an invalid or redacted response; no further action taken"
            ) from error

    def _blob(self, *arguments: str) -> Any:
        state = self.config.spec.terraform
        return self._az(
            "storage",
            "blob",
            *arguments,
            "--account-name",
            state.state_storage_account,
            "--container-name",
            state.state_container,
            "--auth-mode",
            "login",
        )

    def _terraform(self, *arguments: str) -> str:
        return self._run("terraform", f"-chdir={self.directory}", *arguments)

    def _owned(self, resource: Any) -> None:
        tags = resource.get("tags", {}) if isinstance(resource, dict) else {}
        if any(
            tags.get(key) != value
            for key, value in {
                "aiks-managed": "true",
                "aiks-purpose": "terraform-state",
                "aiks-owner": owner(self.config),
            }.items()
        ):
            raise ValueError("backend ownership tags do not match; refusing mutation")

    def inventory(self) -> list[dict[str, Any]]:
        state = self.config.spec.terraform
        group = self._az("group", "show", "--name", state.state_resource_group)
        self._owned(group)
        account = self._az("storage", "account", "show", "--ids", self.account_id)
        self._owned(account)
        rules = account.get("networkRuleSet")
        if (
            account.get("allowSharedKeyAccess") is not False
            or account.get("allowBlobPublicAccess") is not False
            or account.get("enableHttpsTrafficOnly") is not True
            or account.get("minimumTlsVersion") not in {"TLS1_2", "TLS1_3"}
            or not isinstance(rules, dict)
            or rules.get("defaultAction") != "Deny"
            or rules.get("bypass") != "None"
        ):
            raise ValueError(
                "backend security posture drift: require no shared keys/public blobs, "
                "HTTPS with TLS 1.2+, and deny-default networking without bypass"
            )
        blobs = self._blob("list", "--num-results", "*")
        if not isinstance(blobs, list) or not all(
            isinstance(blob, dict) and isinstance(blob.get("name"), str) for blob in blobs
        ):
            raise ValueError("unable to verify backend blob inventory")
        return blobs

    @contextmanager
    def session(self) -> Iterator[None]:
        if fcntl is None:
            raise ValueError("state mutations require POSIX file locking (macOS or Linux)")
        directories = [self.directory.parent.parent, self.directory.parent, self.directory]
        for directory in directories:
            if directory.is_symlink():
                raise ValueError("state working directory must not be a symbolic link")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        descriptor = os.open(
            self.directory / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("another local backend operation is active") from error
            if any(
                path.is_symlink()
                for path in self.directory.rglob("*")
                if ".terraform" not in path.parts
            ):
                raise ValueError("state workspace contains a symbolic link")
            yield
        finally:
            os.close(descriptor)

    def _download(self, path: Path, lease: str | None = None) -> dict[str, Any]:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        lease_args = ("--lease-id", lease) if lease else ()
        self._blob(
            "download",
            "--name",
            BOOTSTRAP_KEY,
            "--file",
            str(path),
            "--overwrite",
            "true",
            *lease_args,
        )
        path.chmod(0o600)
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("bootstrap recovery state is unexpectedly large")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            check_recovery(document, self.config, self.subscription)
        except (json.JSONDecodeError, AttributeError, TypeError, KeyError) as error:
            raise ValueError("invalid bootstrap recovery state") from error
        return dict(document)

    def status(self) -> dict[str, Any]:
        blobs = self.inventory()
        return {
            "environment": self.config.spec.environment,
            "backend": self.config.spec.terraform.state_storage_account,
            "stateKeys": [blob["name"] for blob in blobs],
            "leases": [blob_lease(blob) for blob in blobs],
        }

    def _check_unused_services(self) -> None:
        manager = self._az("cloud", "show", "--query", "endpoints.resourceManager")
        if not isinstance(manager, str) or not manager.startswith("https://"):
            raise ValueError("unable to determine the Azure management endpoint")
        for service, collection in (
            ("fileServices", "shares"),
            ("queueServices", "queues"),
            ("tableServices", "tables"),
        ):
            response = self._az(
                "rest",
                "--method",
                "get",
                "--url",
                f"{manager.rstrip('/')}{self.account_id}/{service}/default/"
                f"{collection}?api-version=2023-05-01",
            )
            if (
                not isinstance(response, dict)
                or response.get("value") != []
                or response.get("nextLink")
            ):
                raise ValueError(
                    "backend contains unrelated storage data or an incomplete inventory"
                )

    def bootstrap(self) -> dict[str, Any]:
        operator = self._az("ad", "signed-in-user", "show", "--query", "id")
        values = bootstrap_variables(self.config, operator)
        with self.session(), asset_root() as assets:
            for source in (assets / "bootstrap").iterdir():
                if source.suffix == ".tf" or source.name == ".terraform.lock.hcl":
                    shutil.copyfile(source, self.directory / source.name)
            write_json(self.directory / "inputs.tfvars.json", values)
            backend_file = self.directory / "backend.tf.json"
            remote_config = {
                "terraform": {"backend": {"azurerm": backend(self.config, bootstrap=True)}}
            }
            exists = self._az(
                "group", "exists", "--name", self.config.spec.terraform.state_resource_group
            )
            if not isinstance(exists, bool):
                raise ValueError("unable to determine backend existence")
            blobs = self.inventory() if exists else []
            check_blobs(blobs, environment_key=backend(self.config)["key"])
            local_path = self.directory / "terraform.tfstate"
            local_state = None
            if local_path.exists():
                if local_path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError("local bootstrap state is unexpectedly large")
                local_state = json.loads(local_path.read_text(encoding="utf-8"))
                check_recovery(local_state, self.config, self.subscription, allow_empty=bool(blobs))
            if blobs:
                recovery = self.directory / f"resume-{uuid4()}.tfstate"
                remote_state = self._download(recovery)
                if (
                    local_state is not None
                    and local_state["resources"]
                    and local_state != remote_state
                ):
                    raise ValueError(
                        "local and remote bootstrap state differ; "
                        "recover explicitly before retrying"
                    )
                write_json(backend_file, remote_config)
                self._terraform(
                    "init", "-reconfigure", "-input=false", "-lockfile=readonly", "-no-color"
                )
            else:
                if backend_file.exists():
                    raise ValueError(
                        "remote bootstrap state is missing; "
                        "preserve local state and recover before retrying"
                    )
                self._terraform("init", "-input=false", "-lockfile=readonly", "-no-color")
            self._terraform(
                "plan",
                "-input=false",
                "-no-color",
                "-var-file=inputs.tfvars.json",
                "-out=bootstrap.tfplan",
            )
            (self.directory / "bootstrap.tfplan").chmod(0o600)
            self._terraform("apply", "-input=false", "-no-color", "bootstrap.tfplan")
            if not blobs:
                fresh = self.inventory()
                if fresh:
                    raise ValueError(
                        "remote state appeared during bootstrap; refusing to overwrite it"
                    )
                check_recovery(
                    json.loads((self.directory / "terraform.tfstate").read_text()),
                    self.config,
                    self.subscription,
                )
                write_json(backend_file, remote_config)
                self._terraform(
                    "init",
                    "-migrate-state",
                    "-force-copy",
                    "-input=false",
                    "-lockfile=readonly",
                    "-no-color",
                )
            verified = self.inventory()
            check_blobs(verified, environment_key=backend(self.config)["key"])
            if not any(blob["name"] == BOOTSTRAP_KEY for blob in verified):
                raise ValueError("bootstrap migration was not verified")
            self._download(self.directory / f"verified-{uuid4()}.tfstate")
            return {
                "environment": self.config.spec.environment,
                "phase": "remote-state-verified",
                "backend": self.config.spec.terraform.state_storage_account,
            }

    def _check_environment_absent(self) -> None:
        groups = self._az("group", "list")
        if not isinstance(groups, list) or any(
            not isinstance(group, dict) or not isinstance(group.get("name"), str)
            for group in groups
        ):
            raise ValueError("environment absence cannot be verified")
        prefix = f"rg-{owner(self.config)}-".lower()
        if any(group["name"].lower().startswith(prefix) for group in groups):
            raise ValueError("environment still exists or its absence cannot be verified")

    def destroy(
        self,
        *,
        confirmed_environment: str,
        allow_production: bool = False,
        delete_recovery: bool = False,
    ) -> dict[str, Any]:
        if confirmed_environment != self.config.spec.environment:
            raise ValueError("environment confirmation does not match")
        if self.config.spec.environment == "production" and not allow_production:
            raise ValueError("production backend deletion requires an explicit override")
        with self.session():
            blobs = self.inventory()
            check_blobs(blobs)
            if len(blobs) != 1:
                raise ValueError("a verified bootstrap state is required before backend deletion")
            self._check_environment_absent()
            resources = self._az(
                "resource",
                "list",
                "--resource-group",
                self.config.spec.terraform.state_resource_group,
            )
            if not isinstance(resources, list) or {
                resource.get("id", "").lower() for resource in resources
            } != {self.account_id.lower()}:
                raise ValueError("backend group contains unexpected resources")
            containers = self._az(
                "storage",
                "container-rm",
                "list",
                "--storage-account",
                self.config.spec.terraform.state_storage_account,
                "--resource-group",
                self.config.spec.terraform.state_resource_group,
            )
            if not isinstance(containers, list) or [
                container.get("name") for container in containers
            ] != [self.config.spec.terraform.state_container]:
                raise ValueError("backend account contains unexpected containers")
            self._check_unused_services()
            lease = str(uuid4())
            self._blob(
                "lease",
                "acquire",
                "--blob-name",
                BOOTSTRAP_KEY,
                "--lease-duration",
                "-1",
                "--proposed-lease-id",
                lease,
            )
            deleted = False
            recovery = self.directory / f"recovery-{uuid4()}.tfstate"
            try:
                self._download(recovery, lease)
                check_blobs(self.inventory(), allow_bootstrap_lease=True)
                self._check_environment_absent()
                self._az(
                    "group",
                    "delete",
                    "--name",
                    self.config.spec.terraform.state_resource_group,
                    "--yes",
                )
                if (
                    self._az(
                        "group", "exists", "--name", self.config.spec.terraform.state_resource_group
                    )
                    is not False
                ):
                    raise ValueError(
                        "backend deletion could not be verified; recovery copy retained"
                    )
                deleted = True
            finally:
                if not deleted:
                    try:
                        self._blob(
                            "lease", "release", "--blob-name", BOOTSTRAP_KEY, "--lease-id", lease
                        )
                    except ValueError:
                        LOGGER.warning(
                            "backend lease release was not verified; "
                            "inspect the backend before retrying"
                        )
            if delete_recovery:
                recovery.unlink()
            receipt = {
                "environment": self.config.spec.environment,
                "backendDeleted": True,
                "recoveryCopy": "deleted" if delete_recovery else str(recovery),
                "note": "Other local recovery copies may remain; inspect the private workspace.",
            }
            write_json(self.directory / "cleanup-receipt.json", receipt)
            return receipt
