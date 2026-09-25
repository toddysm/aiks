"""Offline Terraform input and backend guard tests."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from aiks.config import load_environment_config
from aiks.engines import bicep, terraform

CONFIG = Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config"
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    "patch",
    [
        {"lineage": None},
        {"lineage": ""},
        {"serial": True},
        {"serial": -1},
        {"serial": "1"},
        {"outputs": None},
        {"outputs": []},
        {"resources": None},
        {"resources": [{"mode": "data"}]},
    ],
)
def test_environment_state_cleanup_requires_verifiable_metadata(patch):
    document = {
        "version": 4,
        "lineage": "synthetic-lineage",
        "serial": 1,
        "outputs": {},
        "resources": [],
    }
    terraform.check_empty_environment_state(document)
    with pytest.raises(ValueError):
        terraform.check_empty_environment_state({**document, **patch})


def test_empty_state_accepts_only_well_formed_data_instances():
    document = {
        "version": 4,
        "lineage": "synthetic-lineage",
        "serial": 1,
        "outputs": {},
        "resources": [
            {
                "mode": "data",
                "type": "azurerm_client_config",
                "name": "current",
                "instances": [{"attributes": {}}],
            }
        ],
    }
    terraform.check_empty_environment_state(document)
    document["resources"][0]["instances"] = [None]
    with pytest.raises(ValueError, match="instances"):
        terraform.check_empty_environment_state(document)
    for invalid in (None, [], {"version": 3}):
        with pytest.raises(ValueError):
            terraform.check_empty_environment_state(invalid)


@pytest.mark.parametrize("root", ["bootstrap", "modules/cluster"])
def test_pinned_provider_schema(root: str) -> None:
    directory = CONFIG.parent / "terraform" / root
    executable = shutil.which("terraform")
    if executable is None or not (directory / ".terraform/providers").exists():
        if os.environ.get("AIKS_REQUIRE_TERRAFORM") == "1":
            pytest.fail("Terraform and locked provider initialization are required")
        pytest.skip("provider schema checks run in the Terraform CI job")
    result = subprocess.run(
        [executable, f"-chdir={directory}", "providers", "schema", "-json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    schemas = json.loads(result.stdout)["provider_schemas"]
    if root == "bootstrap":
        container = schemas["registry.terraform.io/hashicorp/azurerm"]["resource_schemas"][
            "azurerm_storage_container"
        ]["block"]["attributes"]
        assert container["storage_account_id"]["required"]
        assert "resource_manager_id" not in container
        assert container["url"]["computed"]
        subnet = schemas["registry.terraform.io/hashicorp/azurerm"]["resource_schemas"][
            "azurerm_subnet"
        ]["block"]
        assert "service_endpoints" not in subnet["attributes"]
        endpoint = subnet["block_types"]["service_endpoint"]
        assert endpoint["nesting_mode"] == "list"
        assert endpoint["block"]["attributes"]["service"]["required"]
    else:
        resource = schemas["registry.terraform.io/azure/azapi"]["resource_schemas"][
            "azapi_resource"
        ]["block"]["attributes"]
        assert resource["response_export_values"]["type"] == "dynamic"
        assert resource["replace_triggers_external_values"]["type"] == "dynamic"


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


@pytest.mark.parametrize("flat", [False, True])
def test_normalize_lease_shapes(flat: bool) -> None:
    for lease_status in ("unlocked", "locked"):
        metadata = {"status": lease_status, "state": "available", "duration": None}
        properties = (
            {"lease" + name.capitalize(): value for name, value in metadata.items()}
            if flat
            else {"lease": metadata}
        )
        blob = {"name": "bootstrap.tfstate", "properties": properties}
        assert terraform.blob_lease(blob) == {"status": lease_status, "state": "available"}
        if lease_status == "locked":
            with pytest.raises(ValueError, match="active state lease"):
                terraform.check_blobs([blob])
        else:
            terraform.check_blobs([blob])


@pytest.mark.parametrize(
    "properties",
    [
        None,
        "invalid",
        {"lease": "invalid"},
        {"leaseStatus": []},
        {"leaseStatus": "locked", "lease": {"status": "unlocked"}},
    ],
)
def test_reject_ambiguous_lease_metadata(properties: object) -> None:
    with pytest.raises(ValueError):
        terraform.blob_lease({"properties": properties})


def test_accept_unused_backend() -> None:
    terraform.check_blobs([])
    terraform.check_blobs(
        [{"name": "bootstrap.tfstate", "properties": {"lease": {"status": "unlocked"}}}]
    )


@pytest.mark.parametrize(
    "name,lease", [("unrelated.tfstate", "unlocked"), ("dev/environment.tfstate", "locked")]
)
def test_bootstrap_update_rejects_unknown_or_locked_key(name: str, lease: str) -> None:
    blobs = [
        {"name": "bootstrap.tfstate", "properties": {"lease": {"status": "unlocked"}}},
        {"name": name, "properties": {"lease": {"status": lease}}},
    ]
    with pytest.raises(ValueError):
        terraform.check_blobs(blobs, environment_key="dev/environment.tfstate")


def test_bootstrap_update_requires_bootstrap_key() -> None:
    with pytest.raises(ValueError, match="without bootstrap"):
        terraform.check_blobs(
            [{"name": "dev/environment.tfstate", "properties": {"lease": {"status": "unlocked"}}}],
            environment_key="dev/environment.tfstate",
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


@pytest.mark.parametrize(
    "resource",
    [
        None,
        "invalid",
        {},
        {"mode": [], "type": "azurerm_resource_group"},
        {"mode": "managed", "type": []},
        {"mode": "managed", "type": "azurerm_resource_group", "instances": None},
        {"mode": "managed", "type": "azurerm_resource_group", "instances": [None]},
        {"mode": "managed", "type": "azurerm_resource_group", "instances": [{"attributes": []}]},
        {
            "mode": "managed",
            "type": "azurerm_resource_group",
            "instances": [{"attributes": {"id": 1}}],
        },
        {
            "mode": "managed",
            "type": "azurerm_role_assignment",
            "instances": [{"attributes": {"id": "test", "scope": []}}],
        },
    ],
)
def test_recovery_rejects_malformed_nested_shapes(resource: object) -> None:
    config = load_environment_config(CONFIG / "dev.example.yaml")
    document = {"version": 4, "serial": 1, "lineage": "test", "resources": [resource]}
    with pytest.raises(ValueError):
        terraform.check_recovery(document, config, SUBSCRIPTION)
