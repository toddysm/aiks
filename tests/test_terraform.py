"""Offline Terraform input and backend guard tests."""

import json
from pathlib import Path

import pytest

from aiks.config import load_environment_config
from aiks.engines import bicep, terraform

CONFIG = Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config"
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_input_parity(environment: str) -> None:
    config = load_environment_config(CONFIG / f"{environment}.example.yaml")
    assert terraform.variables(config)["config"] == {
        name: entry["value"] for name, entry in bicep.parameters(config)["parameters"].items()
    }
    assert terraform.backend(config)["key"] != terraform.backend(config, bootstrap=True)["key"]
    assert terraform.backend(config)["use_azuread_auth"] is True
    assert terraform.environment_group(config, SUBSCRIPTION).startswith(
        "rg-" + terraform.owner(config)
    )


def test_bootstrap_inputs_and_assets(tmp_path: Path) -> None:
    config = load_environment_config(CONFIG / "dev.example.yaml")
    value = terraform.bootstrap_variables(config, SUBSCRIPTION)
    assert value["config"]["allowed_ip_ranges"] == ["203.0.113.10/32"]
    destination = tmp_path / "inputs.json"
    terraform.write_json(destination, value)
    assert json.loads(destination.read_text()) == value
    assert destination.stat().st_mode & 0o777 == 0o600
    with terraform.asset_root() as root:
        assert (root / "bootstrap/main.tf").is_file()
    config = load_environment_config(CONFIG / "production.example.yaml")
    with pytest.raises(ValueError, match="allowedIpRanges"):
        terraform.bootstrap_variables(config, SUBSCRIPTION)


@pytest.mark.parametrize(
    "blobs",
    [
        None,
        [{}],
        [{"name": "environment.tfstate"}],
        [{"name": "bootstrap.tfstate"}],
        [{"name": "bootstrap.tfstate", "properties": {"lease": {"status": "locked"}}}],
    ],
)
def test_refuse_unsafe_blob_inventory(blobs: object) -> None:
    with pytest.raises(ValueError):
        terraform.check_blobs(blobs)


def test_accept_unused_backend() -> None:
    terraform.check_blobs([])
    terraform.check_blobs(
        [{"name": "bootstrap.tfstate", "properties": {"lease": {"status": "unlocked"}}}]
    )


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_environment_destroy_guards(environment: str, tmp_path: Path) -> None:
    config = load_environment_config(CONFIG / f"{environment}.example.yaml")
    with terraform.asset_root() as assets:
        root = assets / "environment"
        with pytest.raises(ValueError, match="confirmation"):
            terraform.destroy_command(
                config, root, tmp_path / "inputs", confirmed_environment="wrong"
            )
        if environment == "production":
            with pytest.raises(ValueError, match="override"):
                terraform.destroy_command(
                    config, root, tmp_path / "inputs", confirmed_environment=environment
                )
        with pytest.raises(ValueError, match="bootstrap"):
            terraform.destroy_command(
                config,
                assets / "bootstrap",
                tmp_path / "inputs",
                confirmed_environment=environment,
                allow_production=True,
            )
        result = terraform.destroy_command(
            config,
            root,
            tmp_path / "inputs",
            confirmed_environment=environment,
            allow_production=True,
        )
        assert result[2] == "destroy" and "-auto-approve" in result


@pytest.mark.parametrize(
    "state",
    [None, {}, {"version": 4}, {"version": 4, "serial": 1, "lineage": "test", "resources": []}],
)
def test_refuse_unverified_recovery_state(state: object) -> None:
    config = load_environment_config(CONFIG / "dev.example.yaml")
    with pytest.raises(ValueError):
        terraform.check_recovery(state, config, SUBSCRIPTION)
