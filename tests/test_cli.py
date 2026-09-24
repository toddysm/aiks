from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from aiks.cli import cli

ROOT = Path(__file__).parents[1]
DEV = ROOT / "infrastructure" / "aks-automatic" / "config" / "dev.example.yaml"
PROD = ROOT / "infrastructure" / "aks-automatic" / "config" / "production.example.yaml"


def test_help_exposes_accepted_command_groups() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in ("config", "infra", "state", "local", "workload"):
        assert command in result.output


def test_console_entry_point_generates_zsh_completion() -> None:
    result = CliRunner().invoke(
        cli,
        [],
        prog_name="aiks",
        env={"_AIKS_COMPLETE": "zsh_source"},
    )

    assert result.exit_code == 0
    assert "#compdef aiks" in result.output
    assert "_aiks_completion" in result.output


def test_validate_writes_redacted_machine_result(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    result = CliRunner().invoke(
        cli,
        [
            "infra",
            "validate",
            "--config",
            str(DEV),
            "--engine",
            "bicep",
            "--json-output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(output.read_text())
    assert payload["succeeded"] is True
    assert payload["context"] == {"engine": "bicep", "environment": "dev", "location": "westus3"}
    assert "correlationId" in payload
    assert "operation=infra.validate" in result.output


def test_preflight_failure_is_structured(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise ValueError("operator lacks deployment permissions")

    monkeypatch.setattr("aiks.cli.InfrastructureRuntime", fail)
    result = CliRunner().invoke(
        cli, ["infra", "preflight", "--config", str(DEV), "--engine", "terraform"]
    )

    assert result.exit_code == 1
    assert "operator lacks deployment permissions" in result.output


@pytest.mark.parametrize("operation", ["bootstrap", "status"])
def test_state_commands_write_results(
    operation: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Backend:
        subscription = "test-subscription"

        def bootstrap(self) -> dict[str, str]:
            return {"phase": "remote-state-verified"}

        def status(self) -> dict[str, str]:
            return {"phase": "inspected"}

    monkeypatch.setattr("aiks.cli.StateBackend", lambda config: Backend())
    output = tmp_path / "result.json"
    result = CliRunner().invoke(
        cli, ["state", operation, "--config", str(DEV), "--json-output", str(output)], input="y\n"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["succeeded"]


def test_state_failure_result_is_redacted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail(config: object) -> None:
        raise ValueError("Bearer private-token")

    monkeypatch.setattr("aiks.cli.StateBackend", fail)
    output = tmp_path / "failure.json"
    result = CliRunner().invoke(
        cli, ["state", "status", "--config", str(DEV), "--json-output", str(output)]
    )
    assert result.exit_code == 1
    assert "private-token" not in result.output + output.read_text()
    assert not json.loads(output.read_text())["succeeded"]


def test_production_destroy_requires_override() -> None:
    result = CliRunner().invoke(
        cli, ["infra", "destroy", "--config", str(PROD), "--engine", "bicep"]
    )

    assert result.exit_code == 2
    assert "--allow-production-destroy" in result.output


def test_destroy_requires_typed_environment() -> None:
    result = CliRunner().invoke(
        cli,
        ["infra", "destroy", "--config", str(DEV), "--engine", "terraform"],
        input="wrong\n",
    )

    assert result.exit_code == 1
    assert "did not match" in result.output


def test_confirmed_dev_destroy_reaches_runtime(monkeypatch) -> None:
    class Runtime:
        phase = "cleanup"

        def destroy(self, **kwargs):
            assert kwargs["confirmed_environment"] == "dev"
            return {"environmentDeleted": True}

    monkeypatch.setattr("aiks.cli.InfrastructureRuntime", lambda *args, **kwargs: Runtime())
    result = CliRunner().invoke(
        cli,
        ["infra", "destroy", "--config", str(DEV), "--engine", "terraform"],
        input="dev\n",
    )

    assert result.exit_code == 0, result.output
    assert "environmentDeleted" in result.output


def test_confirmed_production_state_destroy_reaches_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Backend:
        subscription = "test-subscription"

        def destroy(self, **kwargs: object) -> dict[str, bool]:
            assert kwargs["confirmed_environment"] == "production"
            assert kwargs["allow_production"] is True
            return {"backendDeleted": True}

    monkeypatch.setattr("aiks.cli.StateBackend", lambda config: Backend())
    result = CliRunner().invoke(
        cli,
        ["state", "destroy", "--config", str(PROD), "--allow-production-destroy"],
        input="production\naiks-tfstate-prod\n",
    )

    assert result.exit_code == 0
    assert "backendDeleted" in result.output


def test_infrastructure_commands_dispatch_without_cloud_access(monkeypatch) -> None:
    class Runtime:
        phase = "verified"

        def __getattr__(self, name):
            return lambda **kwargs: {"operation": name}

    monkeypatch.setattr("aiks.cli.InfrastructureRuntime", lambda *args, **kwargs: Runtime())
    invocations = [
        (["infra", "plan", "--config", str(DEV), "--engine", "bicep"], "#11"),
        (["infra", "deploy", "--config", str(DEV), "--engine", "terraform"], "#11"),
        (["infra", "verify", "--config", str(DEV)], "#11"),
    ]

    for arguments, _issue in invocations:
        result = CliRunner().invoke(cli, arguments, input="dev\n")
        assert result.exit_code == 0, result.output
        assert "succeeded" in result.output


@pytest.mark.parametrize(
    "operation", ["preflight", "plan", "deploy", "verify", "destroy", "exercise-alerts"]
)
def test_infrastructure_results_and_confirmations(operation, monkeypatch, tmp_path):
    class Runtime:
        phase = "verified"

        def __getattr__(self, name):
            return lambda **kwargs: {"operation": name}

    monkeypatch.setattr("aiks.cli.InfrastructureRuntime", lambda *args, **kwargs: Runtime())
    output = tmp_path / "result.json"
    arguments = [
        "infra",
        operation,
        "--config",
        str(DEV),
        "--engine",
        "bicep",
        "--json-output",
        str(output),
    ]
    if operation == "destroy":
        arguments += ["--allow-partial-cleanup"]
    result = CliRunner().invoke(cli, arguments, input="dev\n")
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["succeeded"] is True


@pytest.mark.parametrize(
    "operation,flag",
    [("deploy", "--allow-production-deploy"), ("exercise-alerts", "--allow-production-change")],
)
def test_production_mutations_require_override_and_confirmation(operation, flag, monkeypatch):
    monkeypatch.setattr(
        "aiks.cli.InfrastructureRuntime",
        lambda *args, **kwargs: pytest.fail("must refuse before runtime construction"),
    )
    arguments = ["infra", operation, "--config", str(PROD), "--engine", "bicep"]
    assert flag in CliRunner().invoke(cli, arguments).output
    result = CliRunner().invoke(cli, [*arguments, flag], input="wrong\n")
    assert result.exit_code == 1 and "did not match" in result.output


def test_infrastructure_failure_result_is_redacted(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ValueError("Bearer private-value")

    monkeypatch.setattr("aiks.cli.InfrastructureRuntime", fail)
    output = tmp_path / "failure.json"
    result = CliRunner().invoke(
        cli,
        [
            "infra",
            "plan",
            "--config",
            str(DEV),
            "--engine",
            "terraform",
            "--json-output",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "private-value" not in result.output + output.read_text()
    assert not json.loads(output.read_text())["succeeded"]


def test_schema_command_writes_schema(tmp_path: Path) -> None:
    output = tmp_path / "schema.json"
    result = CliRunner().invoke(cli, ["config", "schema", "--output", str(output)])

    assert result.exit_code == 0
    assert json.loads(output.read_text())["title"] == "EnvironmentConfig"


@pytest.mark.parametrize(
    "operation", ["build", "install", "verify", "upgrade", "rollback", "uninstall"]
)
def test_workload_cli_dispatch(
    operation: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Runtime:
        def __getattr__(self, name: str) -> object:
            return lambda *args, **kwargs: {"operation": name}

    monkeypatch.setattr("aiks.cli.WorkloadRuntime", lambda *args, **kwargs: Runtime())
    output = tmp_path / "result.json"
    result = CliRunner().invoke(
        cli,
        [
            "workload",
            operation,
            "--config",
            str(DEV),
            "--target",
            "kind",
            "--json-output",
            str(output),
        ],
        input="aiks-readiness\n",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["succeeded"]


def test_local_delete_requires_matching_name() -> None:
    result = CliRunner().invoke(cli, ["local", "delete", "--config", str(DEV)], input="wrong\n")
    assert result.exit_code == 1 and "confirmation did not match" in result.output


def test_aks_commands_require_explicit_context() -> None:
    result = CliRunner().invoke(
        cli, ["workload", "verify", "--config", str(DEV), "--target", "aks"]
    )
    assert result.exit_code == 1 and "explicit --kubeconfig" in result.output


@pytest.mark.parametrize(
    "payload",
    [
        '{"kubeconfig":"output-private-sentinel"}',
        '{"unexpected":"output-private-sentinel"}',
        '{"environment":"output-private-sentinel"}',
        "output-private-sentinel",
    ],
)
def test_invalid_foundation_outputs_omit_input_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid outputs must fail before runtime construction")

    monkeypatch.setattr("aiks.cli.WorkloadRuntime", forbidden)
    outputs = tmp_path / "outputs.json"
    outputs.write_text(payload)
    result_file = tmp_path / "result.json"
    result = CliRunner().invoke(
        cli,
        [
            "workload",
            "verify",
            "--config",
            str(DEV),
            "--target",
            "aks",
            "--outputs",
            str(outputs),
            "--json-output",
            str(result_file),
        ],
    )
    assert result.exit_code == 1
    assert "invalid configuration" in result.output
    assert "output-private-sentinel" not in result.output + result_file.read_text()
    assert not json.loads(result_file.read_text())["succeeded"]


def test_invalid_configuration_redacts_click_error(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEV.read_text())
    raw["spec"]["naming"]["prefix"] = "Bearer abc.def"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    result = CliRunner().invoke(
        cli, ["infra", "validate", "--config", str(path), "--engine", "bicep"]
    )

    assert result.exit_code == 1
    assert "abc.def" not in result.output
    assert "spec.naming.prefix" in result.output
    assert "prefix must be 3-24 lowercase letters" in result.output


def test_secret_shaped_extra_field_never_reaches_click_error(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEV.read_text())
    raw["spec"]["apiKey"] = "arbitrary-secret-value"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    result = CliRunner().invoke(
        cli, ["infra", "validate", "--config", str(path), "--engine", "bicep"]
    )

    assert result.exit_code == 1
    assert "arbitrary-secret-value" not in result.output
    assert "secret-shaped configuration field is prohibited: spec.apiKey" in result.output


def test_validation_error_omits_raw_input_value(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEV.read_text())
    raw["spec"]["unrecognizedField"] = "private-key-value"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    result = CliRunner().invoke(
        cli, ["infra", "validate", "--config", str(path), "--engine", "bicep"]
    )

    assert result.exit_code == 1
    assert "private-key-value" not in result.output
    assert "spec.unrecognizedField" in result.output
