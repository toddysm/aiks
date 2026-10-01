"""Fail-closed local and Azure prerequisites for operator-run deployments."""

from __future__ import annotations

import fnmatch
import json
import re
import socket
from importlib.resources import files
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from aiks.azure import AzureSession, cli_environment, object_response
from aiks.config import EnvironmentConfig
from aiks.process import run_command


def foundation_policy(relative: str) -> dict[str, Any]:
    packaged = files("aiks").joinpath("infrastructure/aks-automatic/" + relative)
    path = (
        packaged
        if packaged.is_file()
        else Path(__file__).resolve().parents[2] / "infrastructure/aks-automatic" / relative
    )
    return dict(json.loads(path.read_text()))


def platform_policy() -> dict[str, Any]:
    return foundation_policy("config/platform.json")


def check_tools(engine: str) -> dict[str, str]:
    versions = {}
    for name, policy in platform_policy()["tools"].items():
        if name in {"bicep", "terraform"} and name != engine:
            continue
        result = run_command(
            [name, *policy["arguments"]], timeout_seconds=30, environment=cli_environment()
        )
        if not result.succeeded:
            raise ValueError(f"required tool unavailable: {name}")
        value = result.stdout
        if "field" in policy:
            document = json.loads(value)
            for field in policy["field"].split("."):
                document = document[field]
            value = document
        match = re.search(r"(?:^|[^0-9])v?(\d+)\.(\d+)\.(\d+)\b", value)
        if match is None:
            raise ValueError(f"unable to determine {name} version")
        version = tuple(int(part) for part in match.groups())
        if version < tuple(policy["minimum"]) or version[0] not in policy["major"]:
            raise ValueError(f"incompatible {name} version")
        versions[name] = ".".join(match.groups())
    return versions


def allows_action(permissions: Any, action: str, *, data: bool = False) -> bool:
    if not isinstance(permissions, list) or not permissions:
        return False
    allowed_key = "dataActions" if data else "actions"
    excluded_key = "notDataActions" if data else "notActions"
    for permission in permissions:
        if (
            not isinstance(permission, dict)
            or not isinstance(permission.get(allowed_key, [] if data else None), list)
            or not isinstance(permission.get(excluded_key, []), list)
        ):
            return False
        patterns = permission.get(allowed_key, []) + permission.get(excluded_key, [])
        if not all(isinstance(pattern, str) for pattern in patterns):
            return False
    return any(
        any(
            fnmatch.fnmatchcase(action.lower(), pattern.lower())
            for pattern in permission.get(allowed_key, [])
        )
        and not any(
            fnmatch.fnmatchcase(action.lower(), pattern.lower())
            for pattern in permission.get(excluded_key, [])
        )
        for permission in permissions
    )


def probe_host(host: str, *, private: bool, timeout: float) -> list[str]:
    try:
        addresses = sorted(
            {str(entry[4][0]) for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        )
        if not addresses:
            raise ValueError("endpoint has no resolvable address")
        if private and any(
            not any(
                ip_address(address) in ip_network(cidr)
                for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
            for address in addresses
        ):
            raise ValueError("private endpoint resolves outside RFC1918 address space")
        for address in addresses:
            with socket.create_connection((address, 443), timeout=timeout):
                pass
        return addresses
    except OSError as error:
        raise ValueError("endpoint DNS or TCP connectivity could not be verified") from error


def _quota_count(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError("regional core quota is malformed")


def required_actions(config: EnvironmentConfig, engine: str = "bicep") -> set[str]:
    policy = platform_policy()
    spec = config.spec
    observability = spec.observability
    production = spec.environment == "production"
    alerts = production or bool(
        observability.action_group_resource_ids or observability.action_group_receivers
    )
    enabled = {
        "privateDns": production or spec.network.private_cluster,
        "privateEndpoints": production,
        "serviceEndpoints": not production,
        "containerInsights": observability.container_insights,
        "managedPrometheus": observability.managed_prometheus,
        "managedGrafana": observability.managed_grafana,
        "actionGroupReceivers": bool(observability.action_group_receivers),
        "alerts": alerts,
        "prometheusAlerts": alerts and observability.managed_prometheus,
        "logAlerts": alerts and observability.container_insights,
    }
    if engine not in policy["engineActions"]:
        raise ValueError("unsupported infrastructure engine")
    actions = set(policy["requiredActions"]) | set(policy["engineActions"][engine])
    for feature, selected in enabled.items():
        if selected:
            actions.update(policy["conditionalActions"][feature])
    resource_actions = {
        action.removesuffix("/write") for action in actions if action.endswith("/write")
    }
    actions.update(resource + "/read" for resource in resource_actions)
    if engine == "terraform":
        actions.update(resource + "/delete" for resource in resource_actions)
    lifecycle = policy["lifecycleActions"]
    actions.update(lifecycle["common"])
    actions.update(lifecycle[engine])
    for feature in ("privateEndpoints", "containerInsights", "prometheusAlerts"):
        if enabled[feature]:
            actions.update(lifecycle[feature])
    if "@sha256:" not in spec.workload.image:
        actions.update(lifecycle["imagePublication"])
    return actions


def check_backend_permissions(config: EnvironmentConfig, azure: AzureSession) -> None:
    from urllib.parse import quote

    state = config.spec.terraform
    scope = (
        f"/subscriptions/{azure.subscription}/resourceGroups/"
        f"{quote(state.state_resource_group, safe='')}"
        "/providers/Microsoft.Storage/storageAccounts/"
        f"{quote(state.state_storage_account, safe='')}"
        f"/blobServices/default/containers/{quote(state.state_container, safe='')}"
    )
    response = object_response(
        azure.json(
            "rest",
            "--method",
            "get",
            "--url",
            f"https://management.azure.com{scope}/providers/Microsoft.Authorization/permissions?api-version=2022-04-01",
        ),
        "Terraform backend permissions",
    )
    if response.get("nextLink"):
        raise ValueError("Terraform backend permission inventory is incomplete")
    missing = sorted(
        action
        for action in platform_policy()["terraformBackendDataActions"]
        if not allows_action(response.get("value"), action, data=True)
    )
    if missing:
        raise ValueError(
            "configured Terraform state container lacks data permissions: " + ", ".join(missing)
        )


def cloud_preflight(
    config: EnvironmentConfig, azure: AzureSession, *, engine: str = "bicep"
) -> dict[str, Any]:
    policy = platform_policy()
    if config.spec.location not in policy["automaticRegions"]:
        raise ValueError("region is not in the reviewed AKS Automatic availability catalog")
    cloud = object_response(azure.json("cloud", "show"), "cloud context")
    if cloud.get("name") != "AzureCloud":
        raise ValueError("this foundation currently supports Azure public cloud only")
    providers = azure.json(
        "provider",
        "list",
        "--query",
        "[].{namespace:namespace,registrationState:registrationState,"
        "resourceTypes:resourceTypes[].{resourceType:resourceType,locations:locations}}",
    )
    if not isinstance(providers, list) or any(
        not isinstance(provider, dict)
        or not isinstance(provider.get("namespace"), str)
        or not provider["namespace"]
        for provider in providers
    ):
        raise ValueError("resource provider registration cannot be verified")
    registered = {
        provider["namespace"].casefold()
        for provider in providers
        if provider.get("registrationState") == "Registered"
    }
    missing = sorted(
        namespace for namespace in policy["providers"] if namespace.casefold() not in registered
    )
    if missing:
        raise ValueError(
            "register required resource providers before deployment: " + ", ".join(missing)
        )
    cluster_provider = next(
        provider
        for provider in providers
        if provider["namespace"].casefold() == "microsoft.containerservice"
    )
    resource_types = cluster_provider.get("resourceTypes")
    if not isinstance(resource_types, list) or any(
        not isinstance(resource, dict)
        or not isinstance(resource.get("resourceType"), str)
        or not resource["resourceType"]
        or not isinstance(resource.get("locations"), list)
        or not all(isinstance(location, str) and location for location in resource["locations"])
        for resource in resource_types
    ):
        raise ValueError("cluster provider resource-type metadata is malformed")
    locations: list[str] = next(
        (
            resource["locations"]
            for resource in resource_types
            if resource["resourceType"].casefold() == "managedclusters"
        ),
        [],
    )
    if config.spec.location not in {location.lower().replace(" ", "") for location in locations}:
        raise ValueError("cluster region is unavailable in this subscription")
    permissions = azure.json(
        "rest",
        "--method",
        "get",
        "--url",
        f"https://management.azure.com/subscriptions/{azure.subscription}/providers/Microsoft.Authorization/permissions?api-version=2022-04-01",
    )
    permissions = object_response(permissions, "effective permissions")
    if permissions.get("nextLink"):
        raise ValueError("subscription permission inventory is incomplete")
    missing_actions = sorted(
        action
        for action in required_actions(config, engine)
        if not allows_action(permissions.get("value"), action)
    )
    if missing_actions:
        raise ValueError(
            "operator lacks required subscription permissions: " + ", ".join(missing_actions)
        )
    if config.spec.observability.managed_prometheus:
        missing_data = sorted(
            action
            for action in policy["prometheusDataActions"]
            if not allows_action(permissions.get("value"), action, data=True)
        )
        if missing_data:
            raise ValueError(
                "operator lacks required monitoring data permissions: " + ", ".join(missing_data)
            )
    extensions = azure.json("extension", "list")
    if not isinstance(extensions, list) or any(
        not isinstance(extension, dict)
        or not isinstance(extension.get("name"), str)
        or not extension["name"]
        for extension in extensions
    ):
        raise ValueError("Azure CLI extension inventory is malformed")
    if any(extension.get("name") == "aks-preview" for extension in extensions):
        raise ValueError("remove the incompatible aks-preview extension before deployment")
    quota = azure.json("vm", "list-usage", "--location", config.spec.location)
    if not isinstance(quota, list):
        raise ValueError("regional quota inventory is unverifiable")
    for item in quota:
        name = object_response(object_response(item, "quota item").get("name"), "quota name")
        if not isinstance(name.get("value"), str) or not name["value"]:
            raise ValueError("regional quota name is malformed")
    cores = next(
        (item for item in quota if item.get("name", {}).get("value", "").lower() == "cores"), None
    )
    if not cores:
        raise ValueError("insufficient or unverifiable regional core quota")
    available = _quota_count(cores.get("limit")) - _quota_count(cores.get("currentValue"))
    if available < config.spec.lifecycle.minimum_available_cores:
        raise ValueError("insufficient or unverifiable regional core quota")
    if config.spec.network.private_cluster:
        if not config.spec.lifecycle.private_probe_hosts:
            raise ValueError(
                "private clusters require lifecycle.privateProbeHosts "
                "for connected-operator preflight"
            )
        for host in config.spec.lifecycle.private_probe_hosts:
            probe_host(host, private=True, timeout=config.spec.lifecycle.connection_timeout_seconds)
    return {
        "providers": "registered",
        "permissions": "verified",
        "regionalQuota": "available",
        "privateConnectivity": "probed" if config.spec.network.private_cluster else "not-required",
        "capacityReservation": "not-guaranteed",
    }
