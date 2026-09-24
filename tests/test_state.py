"""Backend lifecycle tests use mocked process boundaries only."""

import json
from pathlib import Path
from typing import Any

import pytest

from aiks.config import load_environment_config
from aiks.process import CommandResult
from aiks.state import StateBackend

CONFIG = Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config"
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StateBackend:
    def account(*args: Any, **kwargs: Any) -> CommandResult:
        return CommandResult(
            tuple(args[0]),
            0,
            json.dumps({"state": "Enabled", "id": SUBSCRIPTION, "tenantId": SUBSCRIPTION}),
            "",
        )

    monkeypatch.setattr("aiks.state.run_command", account)
    return StateBackend(
        load_environment_config(CONFIG / "dev.example.yaml"), directory=tmp_path / "state"
    )


def test_environment_isolation(service: StateBackend) -> None:
    assert service.environment["ARM_USE_CLI"] == "true"
    assert service.environment["ARM_SUBSCRIPTION_ID"] == SUBSCRIPTION


def test_only_known_reads_retry(service: StateBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[object] = []
    delays: list[int] = []

    def denied(*args: Any, **kwargs: Any) -> CommandResult:
        attempts.append(args)
        return CommandResult(tuple(args[0]), 1, "", "AuthorizationPermissionMismatch")

    monkeypatch.setattr("aiks.state.run_command", denied)
    monkeypatch.setattr("aiks.state.sleep", delays.append)
    with pytest.raises(ValueError, match="failed"):
        service._run("az", "storage", "blob", "list")
    assert len(attempts) == 4 and delays == [1, 2, 4]
    attempts.clear()
    delays.clear()
    with pytest.raises(ValueError, match="failed"):
        service._run("az", "group", "delete")
    assert len(attempts) == 1 and not delays


def test_private_session(service: StateBackend) -> None:
    with service.session():
        assert service.directory.stat().st_mode & 0o777 == 0o700
        with pytest.raises(ValueError, match="another local"), service.session():
            pass


def test_wrong_confirmation(service: StateBackend) -> None:
    with pytest.raises(ValueError, match="confirmation"):
        service.destroy(confirmed_environment="wrong")


def test_nonbootstrap_blob_blocks_delete(
    service: StateBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "inventory", lambda: [{"name": "production.tfstate"}])
    with pytest.raises(ValueError, match="non-bootstrap"):
        service.destroy(confirmed_environment="dev")


def test_ownership_refuses_unrelated_group(service: StateBackend) -> None:
    with pytest.raises(ValueError, match="ownership"):
        service._owned({"tags": {"aiks-managed": "true"}})


def test_failed_and_malformed_process(
    service: StateBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aiks.state.run_command", lambda *args, **kwargs: CommandResult(("az",), 1, "", "denied")
    )
    with pytest.raises(ValueError, match="failed"):
        service._az("account", "show")
    monkeypatch.setattr(
        "aiks.state.run_command", lambda *args, **kwargs: CommandResult(("az",), 0, "not json", "")
    )
    with pytest.raises(ValueError, match="invalid"):
        service._az("account", "show")


def recovery_state(service: StateBackend) -> dict[str, Any]:
    container = f"{service.account_id}/blobServices/default/containers/tfstate"
    return {
        "version": 4,
        "serial": 1,
        "lineage": "test-lineage",
        "resources": [
            {
                "mode": "managed",
                "type": kind,
                "name": name,
                "instances": [{"attributes": attributes}],
            }
            for kind, name, attributes in [
                ("azurerm_resource_group", "backend", {"id": service.group_id}),
                ("azurerm_storage_account", "backend", {"id": service.account_id}),
                ("azurerm_storage_container", "backend", {"id": container}),
                (
                    "azurerm_role_assignment",
                    "operator",
                    {
                        "id": container + "/providers/Microsoft.Authorization/roleAssignments/test",
                        "scope": container,
                    },
                ),
            ]
        ],
    }


class Cloud:
    def __init__(self, service: StateBackend) -> None:
        self.service = service
        self.exists = False
        self.remote = False
        self.environment_exists = False
        self.locked = False
        self.deleted = False
        self.extra_resource = False
        self.extra_container = False
        self.extra_service = False
        self.fail_delete = False
        self.calls: list[tuple[str, ...]] = []
        self.document = recovery_state(service)

    def az(self, *arguments: str) -> Any:
        self.calls.append(arguments)
        if arguments[:2] == ("cloud", "show"):
            return "https://management.azure.com/"
        if arguments[0] == "rest":
            return {"value": [{"name": "unrelated"}] if self.extra_service else []}
        if arguments[:2] == ("ad", "signed-in-user"):
            return SUBSCRIPTION
        if arguments[:2] == ("group", "exists"):
            return self.exists if arguments[-1] == "aiks-tfstate-dev" else self.environment_exists
        if arguments[:2] == ("group", "delete"):
            if self.fail_delete:
                raise ValueError("deletion denied")
            self.exists = False
            self.deleted = True
            return None
        if arguments[:2] == ("group", "show") or arguments[:3] == (
            "storage",
            "account",
            "show",
        ):
            return {
                "tags": {
                    "aiks-managed": "true",
                    "aiks-purpose": "terraform-state",
                    "aiks-owner": "aiks-dev-dev",
                },
                "allowSharedKeyAccess": False,
                "networkRuleSet": {"defaultAction": "Deny"},
            }
        if arguments[:2] == ("resource", "list"):
            return [{"id": self.service.account_id}] + (
                [{"id": "unrelated"}] if self.extra_resource else []
            )
        if arguments[:3] == ("storage", "container-rm", "list"):
            return [{"name": "tfstate"}] + ([{"name": "unrelated"}] if self.extra_container else [])
        if arguments[:2] == ("storage", "blob"):
            assert arguments[-2:] == ("--auth-mode", "login")
            if arguments[2] == "list":
                return (
                    [
                        {
                            "name": "bootstrap.tfstate",
                            "properties": {
                                "lease": {"status": "locked" if self.locked else "unlocked"}
                            },
                        }
                    ]
                    if self.remote
                    else []
                )
            if arguments[2] == "download":
                Path(arguments[arguments.index("--file") + 1]).write_text(json.dumps(self.document))
                return {}
            if arguments[2:4] == ("lease", "acquire"):
                assert not self.locked
                self.locked = True
                return "test-lease"
            if arguments[2:4] == ("lease", "release"):
                self.locked = False
                return None
        raise AssertionError(arguments)

    def terraform(self, *arguments: str) -> str:
        self.calls.append(("terraform", *arguments))
        if arguments[0] == "plan":
            (self.service.directory / "bootstrap.tfplan").touch()
        if arguments[0] == "apply":
            self.exists = True
            (self.service.directory / "terraform.tfstate").write_text(json.dumps(self.document))
        if "-migrate-state" in arguments:
            self.remote = True
        return ""


@pytest.fixture
def cloud(service: StateBackend, monkeypatch: pytest.MonkeyPatch) -> Cloud:
    cloud = Cloud(service)
    monkeypatch.setattr(service, "_az", cloud.az)
    monkeypatch.setattr(service, "_terraform", cloud.terraform)
    return cloud


def test_bootstrap_migration_and_repeat(service: StateBackend, cloud: Cloud) -> None:
    assert service.bootstrap()["phase"] == "remote-state-verified"
    assert cloud.remote
    assert service.bootstrap()["phase"] == "remote-state-verified"
    assert service.status()["stateKeys"] == ["bootstrap.tfstate"]
    assert any("-migrate-state" in call for call in cloud.calls)
    assert any("-reconfigure" in call for call in cloud.calls)
    assert (service.directory / "inputs.tfvars.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("delete_recovery", [False, True])
def test_complete_guarded_cleanup(
    service: StateBackend, cloud: Cloud, delete_recovery: bool
) -> None:
    cloud.exists = cloud.remote = True
    receipt = service.destroy(confirmed_environment="dev", delete_recovery=delete_recovery)
    assert receipt["backendDeleted"] and cloud.deleted
    assert json.loads((service.directory / "cleanup-receipt.json").read_text()) == receipt
    if not delete_recovery:
        assert Path(receipt["recoveryCopy"]).stat().st_mode & 0o777 == 0o600
    else:
        assert not list(service.directory.glob("recovery-*.tfstate"))


@pytest.mark.parametrize(
    "blocker,message",
    [
        ("environment_exists", "environment still"),
        ("extra_resource", "unexpected resources"),
        ("extra_container", "unexpected containers"),
        ("extra_service", "unrelated storage data"),
        ("locked", "active state lease"),
    ],
)
def test_cleanup_blockers(service: StateBackend, cloud: Cloud, blocker: str, message: str) -> None:
    cloud.exists = cloud.remote = True
    setattr(cloud, blocker, True)
    with pytest.raises(ValueError, match=message):
        service.destroy(confirmed_environment="dev")
    assert not cloud.deleted


def test_failed_cleanup_preserves_recovery_and_releases_lease(
    service: StateBackend, cloud: Cloud
) -> None:
    cloud.exists = cloud.remote = cloud.fail_delete = True
    with pytest.raises(ValueError, match="denied"):
        service.destroy(confirmed_environment="dev")
    assert not cloud.locked
    assert len(list(service.directory.glob("recovery-*.tfstate"))) == 1


def test_migration_missing_remote_refuses_overwrite(service: StateBackend, cloud: Cloud) -> None:
    with service.session():
        (service.directory / "backend.tf.json").write_text("{}")
    with pytest.raises(ValueError, match="remote bootstrap state is missing"):
        service.bootstrap()


def test_divergent_local_remote_state_refuses_overwrite(
    service: StateBackend, cloud: Cloud
) -> None:
    cloud.exists = cloud.remote = True
    with service.session():
        local = dict(cloud.document, serial=2)
        (service.directory / "terraform.tfstate").write_text(json.dumps(local))
    with pytest.raises(ValueError, match="local and remote"):
        service.bootstrap()


def test_recovery_rejects_foreign_resources(service: StateBackend, cloud: Cloud) -> None:
    cloud.exists = cloud.remote = True
    cloud.document["resources"][0]["instances"][0]["attributes"]["id"] = "wrong-group"
    with pytest.raises(ValueError, match="does not match"):
        service.destroy(confirmed_environment="dev")
    assert not cloud.deleted and not cloud.locked


def test_workspace_symlink_refused(service: StateBackend) -> None:
    service.directory.parent.mkdir(parents=True)
    service.directory.symlink_to(service.directory.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"), service.session():
        pass
