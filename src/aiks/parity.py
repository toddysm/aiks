"""Offline contracts for equivalent infrastructure inputs and outputs."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from aiks.config import EnvironmentConfig
from aiks.outputs import FoundationOutputs


def compare_inputs(
    config: EnvironmentConfig,
    bicep_document: dict[str, Any],
    terraform_document: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    """Return deterministic failures without accepting undeclared input omissions."""
    failures: list[str] = []
    spec = config.spec.model_dump(mode="json", by_alias=True)
    mappings = contract["inputs"]
    owned = {path.split(".")[0] for path in mappings.values()}
    declared = owned | set(contract["operationalInputs"])
    if declared != set(spec):
        failures.append("configuration input ownership differs from the parity contract")
    bicep_values = {name: entry["value"] for name, entry in bicep_document["parameters"].items()}
    terraform_values = terraform_document["config"]
    for engine, values in (("bicep", bicep_values), ("terraform", terraform_values)):
        if set(values) != set(mappings):
            failures.append(f"{engine}: declared environment input keys differ")
        for name, path in mappings.items():
            expected: Any = spec
            for part in path.split("."):
                expected = expected[part]
            if values.get(name) != expected:
                failures.append(f"{engine}: input {name} differs from configuration")
    output_keys = set(FoundationOutputs.model_json_schema(by_alias=True)["properties"])
    if output_keys != set(contract["outputs"]):
        failures.append("normalized output keys differ from the parity contract")
    return failures


def value_at(document: Any, path: str) -> Any:
    """Read a declared object path, failing closed on missing fields."""
    for part in path.split("."):
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def condition_value(expression: str | bool, config: EnvironmentConfig) -> bool:
    """Evaluate only the declared condition vocabulary, never arbitrary ARM code."""
    if isinstance(expression, bool):
        return expression
    spec = config.spec
    monitoring = spec.observability
    alerts = bool(monitoring.action_group_resource_ids or monitoring.action_group_receivers)
    alerts = alerts or spec.environment == "production"
    conditions = {
        "[variables('production')]": spec.environment == "production",
        "[parameters('network').privateCluster]": spec.network.private_cluster,
        "[parameters('observability').containerInsights]": monitoring.container_insights,
        "[parameters('observability').managedPrometheus]": monitoring.managed_prometheus,
        "[parameters('observability').managedGrafana]": monitoring.managed_grafana,
        (
            "[and(parameters('observability').managedGrafana, "
            "parameters('observability').managedPrometheus)]"
        ): monitoring.managed_grafana and monitoring.managed_prometheus,
        "[not(empty(parameters('observability').actionGroupReceivers))]": bool(
            monitoring.action_group_receivers
        ),
        "[variables('alertsEnabled')]": alerts,
        "[and(parameters('observability').managedPrometheus, variables('alertsEnabled'))]": (
            monitoring.managed_prometheus and alerts
        ),
        "[and(parameters('observability').containerInsights, variables('alertsEnabled'))]": (
            monitoring.container_insights and alerts
        ),
    }
    if expression not in conditions:
        raise ValueError("undeclared resource condition")
    return conditions[expression]


def compiled_inventory(
    template: dict[str, Any], config: EnvironmentConfig
) -> Iterator[dict[str, Any]]:
    resources = template["resources"]
    candidates = resources.values() if isinstance(resources, dict) else resources
    for resource in candidates:
        if not condition_value(resource.get("condition", True), config):
            continue
        if resource["type"].lower() == "microsoft.resources/deployments":
            yield from compiled_inventory(resource["properties"]["template"], config)
            continue
        count = resource.get("copy", {}).get("count", 1)
        if isinstance(count, str):
            match = re.fullmatch(r"\[length\(variables\('([A-Za-z]+)'\)\)\]", count)
            if match is None or not isinstance(template["variables"][match[1]], list):
                raise ValueError("undeclared resource repetition")
            count = len(template["variables"][match[1]])
        for index in range(count):
            yield {
                **resource,
                "templateVariables": template.get("variables", {}),
                "resourceIndex": index,
            }


def compare_inventory(
    template: dict[str, Any],
    plan: dict[str, Any],
    config: EnvironmentConfig,
    contract: dict[str, Any],
) -> list[str]:
    expected = Counter(
        {
            kind.lower(): modes[config.spec.environment]
            for kind, modes in contract["inventory"].items()
            if modes[config.spec.environment]
        }
    )
    failures: list[str] = []
    try:
        bicep_counts = Counter(
            resource["type"].lower() for resource in compiled_inventory(template, config)
        )
        if bicep_counts != expected:
            failures.append(
                f"bicep: inventory differs: {dict(bicep_counts - expected)} / "
                f"missing {dict(expected - bicep_counts)}"
            )
        terraform_counts: Counter[str] = Counter()
        for change in plan["resource_changes"]:
            if change["mode"] != "managed":
                continue
            kind = change["type"]
            native = change["change"]["after"]
            resource_type = (
                native["type"].split("@")[0]
                if kind == "azapi_resource"
                else contract["resourceTypes"][kind]
            )
            terraform_counts[resource_type.lower()] += 1
            if kind == "azurerm_private_endpoint":
                terraform_counts["microsoft.network/privateendpoints/privatednszonegroups"] += len(
                    native["private_dns_zone_group"]
                )
        if terraform_counts != expected:
            failures.append(
                f"terraform: inventory differs: {dict(terraform_counts - expected)} / "
                f"missing {dict(expected - terraform_counts)}"
            )
    except (KeyError, TypeError, ValueError):
        failures.append("inventory contains undeclared conditions, repetitions, or resource types")
    return failures


def compare_bindings(
    template: dict[str, Any],
    plan: dict[str, Any],
    config: EnvironmentConfig,
    contract: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    resources = {
        change["address"]: change["change"]["after"] for change in plan["resource_changes"]
    }
    spec = config.spec.model_dump(mode="json", by_alias=True)
    outputs = plan["output_changes"]["result"]["after"]
    try:
        FoundationOutputs.model_validate(outputs)
    except ValidationError:
        failures.append("terraform: normalized output schema drift")
    for rule in contract["bindings"]:
        if config.spec.environment not in rule.get("environments", ["dev", "production"]):
            continue
        for path, expected in rule["fields"].items():
            try:
                if isinstance(expected, dict):
                    if "config" in expected:
                        expected = value_at(spec, expected["config"])
                    elif "resource" in expected:
                        expected = value_at(resources[expected["resource"]], expected["path"])
                    elif "output" in expected:
                        expected = value_at(outputs, expected["output"])
                    elif "mode" in expected:
                        expected = expected["mode"][config.spec.environment]
                if value_at(resources[rule["address"]], path) != expected:
                    failures.append(f"{rule['address']}: {path} binding drift")
            except (KeyError, TypeError, IndexError):
                failures.append(f"{rule['address']}: {path} missing binding")
    try:
        terraform_roles = Counter(
            contract["roles"][resource["role_definition_name"]]
            for address, resource in resources.items()
            if address.startswith("azurerm_role_assignment.")
        )
        bicep_roles: Counter[str] = Counter()
        for resource in compiled_inventory(template, config):
            if resource["type"] != "Microsoft.Authorization/roleAssignments":
                continue
            expression = resource["properties"]["roleDefinitionId"]
            match = re.fullmatch(
                r"\[subscriptionResourceId\('Microsoft.Authorization/roleDefinitions', (.+)\)\]",
                expression,
            )
            if match is None:
                raise ValueError("role definition expression drift")
            argument = match[1]
            variable = re.fullmatch(r"variables\('([A-Za-z]+)'\)(\[copyIndex\(\)\])?", argument)
            if variable:
                role = resource["templateVariables"][variable[1]]
                if variable[2]:
                    role = role[resource["resourceIndex"]]
            else:
                role = argument.strip("'")
            bicep_roles[role] += 1
        if bicep_roles != terraform_roles:
            failures.append("role definition inventory differs between engines")
    except (KeyError, TypeError, ValueError):
        failures.append("undeclared or malformed role definition")
    if config.spec.environment == "production":
        compiled_rules = next(
            resource
            for resource in compiled_inventory(template, config)
            if resource["type"] == "Microsoft.AlertsManagement/prometheusRuleGroups"
        )
        expected_rules = {
            rule["alert"]: (rule["expression"], rule["severity"], rule["window"])
            for rule in compiled_rules["templateVariables"]["signals"]
        }
        try:
            rules = resources["azurerm_monitor_alert_prometheus_rule_group.signals[0]"]["rule"]
            actual_rules = {
                rule["alert"]: (rule["expression"], rule["severity"], rule["for"]) for rule in rules
            }
            if actual_rules != expected_rules:
                failures.append("operational alert expressions/severities/windows differ")
            action_group = resources["azurerm_monitor_action_group.operators[0]"]["id"]
            if any(
                [action["action_group_id"] for action in rule["action"]] != [action_group]
                for rule in rules
            ):
                failures.append("operational alert notification binding differs")
        except (KeyError, TypeError):
            failures.append("missing operational alert notification policy")
    return failures


def policy_snapshot(document: Any) -> Any:
    """Remove compiler metadata while retaining every policy-bearing expression."""
    if isinstance(document, dict):
        return {
            name: policy_snapshot(value) for name, value in document.items() if name != "metadata"
        }
    if isinstance(document, list):
        return [policy_snapshot(value) for value in document]
    return document


def compare_security(environment_plan: dict[str, Any], config: EnvironmentConfig) -> list[str]:
    """Validate endpoint and monitoring semantics of the evaluated environment fixtures."""
    resources = {
        change["address"].removeprefix("module.cluster."): change["change"]["after"]
        for change in environment_plan["resource_changes"]
    }
    failures: list[str] = []

    def require(condition: bool, name: str) -> None:
        if not condition:
            failures.append(name)

    try:
        spec = config.spec
        production = spec.environment == "production"
        cluster = resources["azapi_resource.cluster"]["body"]["properties"]
        registry = resources["azapi_resource.registry"]["body"]["properties"]
        identity = resources["azapi_resource.cluster"]["identity"][0]
        require(
            identity["type"] == "UserAssigned"
            and identity["identity_ids"]
            == [resources['azurerm_user_assigned_identity.identity["cluster"]']["id"]],
            "cluster managed identity binding",
        )
        api = cluster["apiServerAccessProfile"]
        require(api["enablePrivateCluster"] == spec.network.private_cluster, "private API posture")
        require(
            api["enablePrivateClusterPublicFQDN"] is False and api["disableRunCommand"] is True,
            "API public name/run-command policy",
        )
        require(api["enableVnetIntegration"] is True, "API network integration")
        require(api["subnetId"] == resources["azurerm_subnet.api"]["id"], "API subnet binding")
        require(
            cluster["hostedSystemProfile"]["nodeSubnetID"]
            == resources["azurerm_subnet.user"]["id"],
            "user subnet binding",
        )
        require(
            cluster["hostedSystemProfile"]["systemNodeSubnetID"]
            == resources["azurerm_subnet.system"]["id"],
            "system subnet binding",
        )
        if spec.network.private_cluster:
            require(
                "authorizedIPRanges" not in api
                and api["privateDNSZone"] == resources["azurerm_private_dns_zone.api[0]"]["id"],
                "private API DNS/access policy",
            )
        else:
            require(
                api["authorizedIPRanges"] == spec.network.authorized_ip_ranges
                and "privateDNSZone" not in api,
                "restricted API allowlist",
            )
        require(
            registry["publicNetworkAccess"] == ("Disabled" if production else "Enabled"),
            "registry public access",
        )
        require(
            registry["networkRuleSet"]["ipRules"]
            == [
                {"action": "Allow", "value": cidr}
                for cidr in ([] if production else spec.network.paas_allowed_ip_ranges)
            ],
            "registry IP allowlist",
        )
        subnet_rules = registry["networkRuleSet"]["virtualNetworkRules"]
        expected_subnets = (
            []
            if production
            else [resources[f"azurerm_subnet.{name}"]["id"] for name in ("system", "user")]
        )
        require(
            sorted(rule["virtualNetworkSubnetResourceId"] for rule in subnet_rules)
            == sorted(expected_subnets)
            and all(rule["action"] == "Allow" for rule in subnet_rules),
            "registry subnet allowlist",
        )
        vault = resources["azurerm_key_vault.readiness"]
        expected_ids = (
            []
            if production
            else [resources[f"azurerm_subnet.{name}"]["id"] for name in ("system", "user")]
        )
        require(
            set(vault["network_acls"][0]["virtual_network_subnet_ids"]) == set(expected_ids),
            "vault subnet allowlist",
        )
        for name, field in (
            ("api", "api_server_subnet_cidr"),
            ("system", "system_node_subnet_cidr"),
            ("user", "user_node_subnet_cidr"),
            ("private_endpoint", "private_endpoint_subnet_cidr"),
        ):
            subnet = resources[f"azurerm_subnet.{name}"]
            require(
                subnet["address_prefixes"] == [getattr(spec.network, field)], f"{name} subnet CIDR"
            )
            if name in {"system", "user"}:
                expected_endpoints = (
                    set() if production else {"Microsoft.ContainerRegistry", "Microsoft.KeyVault"}
                )
                require(
                    {endpoint["service"] for endpoint in subnet["service_endpoint"]}
                    == expected_endpoints,
                    f"{name} service endpoints",
                )
        outputs = environment_plan["output_changes"]["result"]["after"]
        require(outputs["readiness"]["internalGateway"] == production, "readiness gateway posture")
        require(
            outputs["readiness"]["clientId"] == outputs["identities"]["readiness"]["clientId"],
            "readiness identity output",
        )
        require(
            outputs["readiness"]["vaultUri"] == outputs["vault"]["uri"], "readiness vault output"
        )
        require(
            outputs["readiness"]["managedPrometheus"] == spec.observability.managed_prometheus,
            "readiness scraping output",
        )
        if production:
            for service, group in (("registry", "registry"), ("vault", "vault")):
                endpoint = resources[f'azurerm_private_endpoint.service["{service}"]']
                connection = endpoint["private_service_connection"][0]
                require(
                    connection["subresource_names"] == [group]
                    and connection["is_manual_connection"] is False,
                    f"{service} private service connection",
                )
                require(
                    connection["private_connection_resource_id"] == outputs[service]["id"],
                    f"{service} endpoint target",
                )
                require(
                    endpoint["subnet_id"] == resources["azurerm_subnet.private_endpoint"]["id"],
                    f"{service} endpoint subnet",
                )
                require(
                    endpoint["private_dns_zone_group"][0]["private_dns_zone_ids"]
                    == [resources[f'azurerm_private_dns_zone.service["{service}"]']["id"]],
                    f"{service} private DNS binding",
                )
            rules = resources["azurerm_monitor_alert_prometheus_rule_group.signals[0]"]
            require(len(rules["rule"]) == 5, "operational alert count")
            require(
                rules["scopes"] == [outputs["monitoring"]["azureMonitorWorkspaceId"]],
                "Prometheus rule workspace",
            )
            for rule in rules["rule"]:
                require(
                    rule["enabled"] is True and bool(rule["action"]), "enabled notification rule"
                )
                require(
                    rule["labels"]
                    == {"environment": "production", "cluster": outputs["cluster"]["name"]},
                    "alert metric dimensions",
                )
    except (KeyError, TypeError, IndexError):
        failures.append("missing or malformed network/monitoring policy")
    return failures


def compiled_resource(template: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    for module in rule["bicepModules"]:
        resources = template["resources"]
        if isinstance(resources, dict):
            deployment = resources[module]
        else:
            deployments = [resource for resource in resources if resource["name"] == module]
            if len(deployments) != 1:
                raise ValueError(f"{module}: expected exactly one compiled module")
            deployment = deployments[0]
        template = deployment["properties"]["template"]
    resources = template["resources"]
    candidates = resources.values() if isinstance(resources, dict) else resources
    matches: list[dict[str, Any]] = [
        resource for resource in candidates if resource["type"] == rule["type"]
    ]
    if len(matches) != 1:
        raise ValueError(f"{rule['id']}: expected exactly one compiled resource")
    return matches[0]


def compare_resources(
    template: dict[str, Any],
    plan: dict[str, Any],
    inputs: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    """Check compiled references and evaluated mock plans against one policy."""
    failures: list[str] = []
    planned = {
        change["address"].removeprefix("module.cluster."): change["change"]["after"]
        for change in plan["resource_changes"]
        if change["mode"] == "managed"
    }
    for rule in contract["resources"]:
        identifier = rule["id"]
        try:
            compiled = compiled_resource(template, rule)
            native = planned[rule["terraformAddress"]]
            payload = native["body"]
            if compiled["apiVersion"] != rule["apiVersion"]:
                failures.append(f"bicep {identifier}: API version drift")
            if native["type"] != f"{rule['type']}@{rule['apiVersion']}":
                failures.append(f"terraform {identifier}: resource type/version drift")
            for path, expected in rule["equal"].items():
                for engine, document in (("bicep", compiled), ("terraform", payload)):
                    if value_at(document, path) != expected:
                        failures.append(f"{engine} {identifier}: {path} differs")
            for path in rule["absent"]:
                for engine, document in (("bicep", compiled), ("terraform", payload)):
                    try:
                        value_at(document, path)
                    except KeyError:
                        continue
                    failures.append(f"{engine} {identifier}: forbidden {path}")
            for path, source in rule["configured"].items():
                parameter, field = source.split(".", 1)
                if value_at(compiled, path) != f"[parameters('{parameter}').{field}]":
                    failures.append(f"bicep {identifier}: {path} input reference drift")
                if value_at(payload, path) != value_at(inputs, source):
                    failures.append(f"terraform {identifier}: {path} input value drift")
        except (KeyError, TypeError, ValueError):
            failures.append(f"{identifier}: missing or malformed resource policy field")
    return failures
