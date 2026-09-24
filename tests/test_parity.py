"""Executable parity specification and intentionally divergent fixtures."""

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_bicep import compiled as compiled

from aiks.config import load_environment_config
from aiks.engines import bicep, terraform
from aiks.outputs import FoundationOutputs
from aiks.parity import (
    compare_bindings,
    compare_inputs,
    compare_inventory,
    compare_resources,
    compare_security,
    compiled_resource,
    policy_snapshot,
)

ROOT = Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic"


def test_compiled_policy_snapshot(compiled):
    expected = json.loads((ROOT / "parity/bicep.snapshot.json").read_text())
    assert policy_snapshot(compiled) == expected


def test_complete_output_schema_contract():
    expected = json.loads((ROOT / "parity/outputs.schema.json").read_text())
    assert FoundationOutputs.model_json_schema(by_alias=True) == expected


def test_policy_snapshot_preserves_expressions():
    source = {
        "metadata": {"compiler": "variable"},
        "resources": [{"properties": {"scope": "[resourceId('example')]"}}],
    }
    assert policy_snapshot(source) == {
        "resources": [{"properties": {"scope": "[resourceId('example')]"}}]
    }


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_input_contract(environment: str) -> None:
    config = load_environment_config(ROOT / "config" / f"{environment}.example.yaml")
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    assert (
        compare_inputs(config, bicep.parameters(config), terraform.variables(config), contract)
        == []
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "network", "ownership", "outputs"])
def test_input_contract_rejects_drift(mutation: str) -> None:
    config = load_environment_config(ROOT / "config/dev.example.yaml")
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    parameters = bicep.parameters(config)
    variables = copy.deepcopy(terraform.variables(config))
    if mutation == "missing":
        del variables["config"]["prefix"]
    elif mutation == "extra":
        variables["config"]["backend"] = {}
    elif mutation == "network":
        variables["config"]["network"]["authorizedIpRanges"] = ["0.0.0.0/0"]
    elif mutation == "ownership":
        del contract["operationalInputs"]["workload"]
    else:
        contract["outputs"].append("credentials")
    assert compare_inputs(config, parameters, variables, contract)


@pytest.fixture(scope="module")
def cluster_plan():
    return mock_plans("modules/cluster", "profiles")


@pytest.fixture(scope="module")
def environment_plan():
    return mock_plans("environment", "environment")


def mock_plans(root, test):
    directory = ROOT / "terraform" / root
    executable = shutil.which("terraform")
    if executable is None or not (directory / ".terraform/providers").exists():
        if os.environ.get("AIKS_REQUIRE_PARITY") == "1":
            pytest.fail("Parity requires Terraform with locked providers initialized")
        pytest.skip("evaluated parity requires initialized Terraform providers")
    result = subprocess.run(
        [
            executable,
            f"-chdir={directory}",
            "test",
            "-json",
            "-verbose",
            f"-filter=tests/{test}.tftest.hcl",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    return {
        event["@testrun"]: event["test_plan"]
        for line in result.stdout.splitlines()
        if (event := json.loads(line)).get("type") == "test_plan"
    }


@pytest.mark.parametrize("profile", ["custom_dev_network", "private_production"])
def test_compiled_and_planned_resources(compiled, cluster_plan, profile):
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    inputs = {
        "network": {
            "podCidr": "10.240.0.0/16",
            "serviceCidr": "10.2.0.0/16",
            "dnsServiceIp": "10.2.0.10",
        },
        "observability": {"containerInsights": False, "managedPrometheus": False},
    }
    assert compare_resources(compiled, cluster_plan[profile], inputs, contract) == []


@pytest.mark.parametrize("engine", ["bicep", "terraform"])
@pytest.mark.parametrize("mutation", ["missing", "profile", "credentials", "network", "bypass"])
def test_resource_policy_rejects_drift(compiled, cluster_plan, engine, mutation):
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    template = copy.deepcopy(compiled)
    plan = copy.deepcopy(cluster_plan["custom_dev_network"])
    inputs = {
        "network": {
            "podCidr": "10.240.0.0/16",
            "serviceCidr": "10.2.0.0/16",
            "dnsServiceIp": "10.2.0.10",
        },
        "observability": {"containerInsights": False, "managedPrometheus": False},
    }
    rule = contract["resources"][1 if mutation == "bypass" else 0]
    if engine == "bicep":
        resource = compiled_resource(template, rule)
        payload = resource
    else:
        resource = next(
            change["change"]["after"]
            for change in plan["resource_changes"]
            if change["address"] == rule["terraformAddress"]
        )
        payload = resource["body"]
    if mutation == "missing":
        del payload["properties"]["networkProfile"]
    elif mutation == "profile":
        payload["sku"]["name"] = "Base"
    elif mutation == "credentials":
        payload["properties"]["disableLocalAccounts"] = False
    elif mutation == "network":
        payload["properties"]["networkProfile"]["serviceCidr"] = "10.0.0.0/8"
    else:
        payload["properties"]["networkRuleBypassOptions"] = "AzureServices"
    assert compare_resources(template, plan, inputs, contract)


@pytest.mark.parametrize(
    "environment,run",
    [
        ("dev", "development"),
        ("production", "production"),
    ],
)
def test_normalized_inventory(compiled, environment_plan, environment, run):
    config = load_environment_config(ROOT / "config" / f"{environment}.example.yaml")
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    plan = environment_plan[run]
    assert compare_inventory(compiled, plan, config, contract) == []


@pytest.mark.parametrize("mutation", ["missing", "extra", "condition", "repeat"])
def test_inventory_rejects_drift(compiled, environment_plan, mutation):
    config = load_environment_config(ROOT / "config/production.example.yaml")
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    template = copy.deepcopy(compiled)
    plan = copy.deepcopy(environment_plan["production"])
    if mutation == "missing":
        plan["resource_changes"].pop()
    elif mutation == "extra":
        plan["resource_changes"].append(copy.deepcopy(plan["resource_changes"][0]))
    elif mutation == "condition":
        template["resources"]["foundation"]["condition"] = "[unrecognized()]"
    else:
        template["resources"]["environmentGroup"]["copy"] = {"count": "[unrecognized()]"}
    assert compare_inventory(template, plan, config, contract)


@pytest.mark.parametrize("environment,run", [("dev", "development"), ("production", "production")])
def test_environment_bindings(compiled, environment_plan, environment, run):
    config = load_environment_config(ROOT / "config" / f"{environment}.example.yaml")
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    assert compare_bindings(compiled, environment_plan[run], config, contract) == []


@pytest.mark.parametrize("environment,run", [("dev", "development"), ("production", "production")])
def test_full_environment_security(compiled, environment_plan, environment, run):
    config = load_environment_config(ROOT / "config" / f"{environment}.example.yaml")
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    plan = environment_plan[run]
    assert (
        compare_resources(
            compiled, plan, config.spec.model_dump(mode="json", by_alias=True), contract
        )
        == []
    )
    assert compare_security(plan, config) == []


@pytest.mark.parametrize(
    "environment,mutation",
    [
        ("dev", "allowlist"),
        ("production", "public"),
        ("production", "identity"),
        ("production", "alerts"),
        ("production", "missing"),
    ],
)
def test_network_policy_rejects_drift(environment_plan, environment, mutation):
    config = load_environment_config(ROOT / "config" / f"{environment}.example.yaml")
    env_plan = copy.deepcopy(
        environment_plan["development" if environment == "dev" else "production"]
    )
    resources = {
        change["address"].removeprefix("module.cluster."): change["change"]["after"]
        for change in env_plan["resource_changes"]
    }
    if mutation == "allowlist":
        resources["azapi_resource.cluster"]["body"]["properties"]["apiServerAccessProfile"][
            "authorizedIPRanges"
        ] = ["0.0.0.0/0"]
    elif mutation == "public":
        resources["azapi_resource.registry"]["body"]["properties"]["publicNetworkAccess"] = (
            "Enabled"
        )
    elif mutation == "identity":
        env_plan["output_changes"]["result"]["after"]["readiness"]["clientId"] = "incorrect"
    elif mutation == "alerts":
        resources["azurerm_monitor_alert_prometheus_rule_group.signals[0]"]["rule"][0][
            "enabled"
        ] = False
    else:
        del resources["azapi_resource.cluster"]["body"]["properties"]["hostedSystemProfile"]
    assert compare_security(env_plan, config)


@pytest.mark.parametrize(
    "mutation", ["role", "scope", "principal", "public", "monitoring", "outputs"]
)
def test_bindings_reject_drift(compiled, environment_plan, mutation):
    config = load_environment_config(ROOT / "config/production.example.yaml")
    contract = json.loads((ROOT / "parity/contract.json").read_text())
    plan = copy.deepcopy(environment_plan["production"])
    resources = {
        change["address"]: change["change"]["after"] for change in plan["resource_changes"]
    }
    if mutation == "role":
        resources["azurerm_role_assignment.vault_reader"]["role_definition_name"] = "Owner"
    elif mutation == "scope":
        resources["azurerm_role_assignment.grafana_reader[0]"]["scope"] = "/subscriptions/incorrect"
    elif mutation == "principal":
        resources["azurerm_role_assignment.vault_reader"]["principal_id"] = "incorrect"
    elif mutation == "public":
        resources["azurerm_key_vault.readiness"]["public_network_access_enabled"] = True
    elif mutation == "monitoring":
        resources["azurerm_monitor_data_collection_rule_association.containers[0]"][
            "data_collection_rule_id"
        ] = "incorrect"
    else:
        del plan["output_changes"]["result"]["after"]["readiness"]["clientId"]
    assert compare_bindings(compiled, plan, config, contract)
