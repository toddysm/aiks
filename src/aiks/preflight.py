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

from aiks.azure import AzureSession
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
        result = run_command([name, *policy["arguments"]], timeout_seconds=30)
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


def allows_action(permissions: Any, action: str) -> bool:
    if not isinstance(permissions, list) or not permissions:
        return False
    for permission in permissions:
        if (
            not isinstance(permission, dict)
            or not isinstance(permission.get("actions"), list)
            or not isinstance(permission.get("notActions", []), list)
        ):
            return False
        patterns = permission["actions"] + permission.get("notActions", [])
        if not all(isinstance(pattern, str) for pattern in patterns):
            return False
    return any(
        any(
            fnmatch.fnmatchcase(action.lower(), pattern.lower())
            for pattern in permission["actions"]
        )
        and not any(
            fnmatch.fnmatchcase(action.lower(), pattern.lower())
            for pattern in permission.get("notActions", [])
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


def cloud_preflight(config: EnvironmentConfig, azure: AzureSession) -> dict[str, Any]:
    policy = platform_policy()
    if config.spec.location not in policy["automaticRegions"]:
        raise ValueError("region is not in the reviewed AKS Automatic availability catalog")
    cloud = azure.json("cloud", "show")
    if cloud.get("name") != "AzureCloud":
        raise ValueError("this foundation currently supports Azure public cloud only")
    providers = azure.json("provider", "list")
    if not isinstance(providers, list):
        raise ValueError("resource provider registration cannot be verified")
    registered = {
        provider["namespace"]
        for provider in providers
        if provider.get("registrationState") == "Registered"
    }
    missing = sorted(set(policy["providers"]) - registered)
    if missing:
        raise ValueError(
            "register required resource providers before deployment: " + ", ".join(missing)
        )
    cluster_provider = next(
        provider for provider in providers if provider["namespace"] == "Microsoft.ContainerService"
    )
    locations: list[str] = next(
        (
            resource.get("locations", [])
            for resource in cluster_provider.get("resourceTypes", [])
            if resource.get("resourceType") == "managedClusters"
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
    if not all(
        allows_action(permissions.get("value"), action) for action in policy["requiredActions"]
    ):
        raise ValueError(
            "operator lacks required subscription deployment or role-assignment permissions"
        )
    extensions = azure.json("extension", "list")
    if any(extension.get("name") == "aks-preview" for extension in extensions):
        raise ValueError("remove the incompatible aks-preview extension before deployment")
    quota = azure.json("vm", "list-usage", "--location", config.spec.location)
    cores = next(
        (item for item in quota if item.get("name", {}).get("value", "").lower() == "cores"), None
    )
    if (
        not cores
        or cores["limit"] - cores["currentValue"] < config.spec.lifecycle.minimum_available_cores
    ):
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
