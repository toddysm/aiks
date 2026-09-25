"""Terraform input mapping and fail-closed state lifecycle contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from uuid import UUID

from aiks.config import EnvironmentConfig

BOOTSTRAP_KEY = "bootstrap.tfstate"


def variables(config: EnvironmentConfig) -> dict[str, Any]:
    spec = config.spec
    return {
        "config": {
            "environment": spec.environment,
            "location": spec.location,
            "prefix": spec.naming.prefix,
            "adminGroupObjectId": str(spec.identity.admin_group_object_id),
            "network": spec.network.model_dump(mode="json", by_alias=True),
            "observability": spec.observability.model_dump(mode="json", by_alias=True),
            "tags": spec.tags,
        }
    }


def owner(config: EnvironmentConfig) -> str:
    return f"{config.spec.naming.prefix}-{config.spec.environment}"


def environment_group(config: EnvironmentConfig, subscription: str) -> str:
    """Return the pre-parity SHA-256 group name for legacy recovery tooling."""
    seed = f"{UUID(subscription)}/{config.spec.naming.prefix}/{config.spec.environment}"
    suffix = hashlib.sha256(seed.encode()).hexdigest()[:8]
    return f"rg-{owner(config)}-{suffix}"


def destroy_command(
    config: EnvironmentConfig,
    directory: Path,
    parameter_file: Path,
    *,
    confirmed_environment: str,
    allow_production: bool = False,
) -> tuple[str, ...]:
    """Build environment-only destruction after the caller verifies remote ownership."""
    if confirmed_environment != config.spec.environment:
        raise ValueError("environment confirmation does not match")
    if config.spec.environment == "production" and not allow_production:
        raise ValueError("production deletion requires an explicit override")
    if directory.name != "environment" or not (directory / "backend.tf").is_file():
        raise ValueError("destroy requires the environment root, never the bootstrap root")
    return (
        "terraform",
        f"-chdir={directory.resolve()}",
        "destroy",
        "-input=false",
        f"-var-file={parameter_file.resolve()}",
        "-auto-approve",
        "-no-color",
    )


def backend(config: EnvironmentConfig, *, bootstrap: bool = False) -> dict[str, Any]:
    state = config.spec.terraform
    return {
        "resource_group_name": state.state_resource_group,
        "storage_account_name": state.state_storage_account,
        "container_name": state.state_container,
        "key": BOOTSTRAP_KEY if bootstrap else f"{owner(config)}/environment.tfstate",
        "use_cli": True,
        "use_azuread_auth": True,
    }


def bootstrap_variables(config: EnvironmentConfig, operator_id: str) -> dict[str, Any]:
    state = config.spec.terraform
    ranges = state.allowed_ip_ranges or config.spec.network.paas_allowed_ip_ranges
    if not ranges and not state.allowed_subnet_ids:
        raise ValueError(
            "configure terraform.allowedIpRanges or allowedSubnetIds for backend access"
        )
    return {
        "config": {
            "environment": config.spec.environment,
            "location": config.spec.location,
            "owner": owner(config),
            "resource_group": state.state_resource_group,
            "storage_account": state.state_storage_account,
            "container": state.state_container,
            "operator_object_id": str(UUID(operator_id)),
            "allowed_ip_ranges": ranges,
            "allowed_subnet_ids": state.allowed_subnet_ids,
        }
    }


@contextmanager
def asset_root() -> Iterator[Path]:
    packaged = files("aiks").joinpath("infrastructure/aks-automatic/terraform")
    if packaged.is_dir():
        with as_file(packaged) as directory:
            yield directory
    else:
        source = Path(__file__).resolve().parents[3] / "infrastructure/aks-automatic/terraform"
        if not (source / "environment/main.tf").is_file():
            raise FileNotFoundError("Terraform assets are missing; reinstall aiks")
        yield source


def blob_lease(blob: Any) -> dict[str, str]:
    """Normalize supported CLI lease shapes without trusting ambiguous metadata."""
    properties = blob.get("properties") if isinstance(blob, dict) else None
    if not isinstance(properties, dict):
        raise ValueError("unable to verify state lease metadata")
    nested = properties.get("lease") or {}
    if not isinstance(nested, dict):
        raise ValueError("unable to verify state lease metadata")
    lease: dict[str, str] = {}
    for field in ("status", "state", "duration"):
        flat = properties.get("lease" + field.capitalize())
        value = nested.get(field)
        if flat is not None and value is not None and flat != value:
            raise ValueError("conflicting state lease metadata")
        value = flat if flat is not None else value
        if value is not None:
            if not isinstance(value, str):
                raise ValueError("invalid state lease metadata")
            lease[field] = value
    if lease.get("status") not in {"unlocked", "locked"}:
        raise ValueError("unable to verify state lease status")
    return lease


def check_blobs(
    blobs: Any, *, allow_bootstrap_lease: bool = False, environment_key: str | None = None
) -> None:
    if not isinstance(blobs, list):
        raise ValueError("unable to verify state-key inventory")
    for blob in blobs:
        allowed_keys = {BOOTSTRAP_KEY, environment_key} if environment_key else {BOOTSTRAP_KEY}
        if not isinstance(blob, dict) or blob.get("name") not in allowed_keys:
            raise ValueError("backend contains a non-bootstrap state key or unrelated blob")
        lease = blob_lease(blob)
        if lease["status"] != "unlocked" and not (
            allow_bootstrap_lease and blob["name"] == BOOTSTRAP_KEY
        ):
            raise ValueError("backend has an active state lease")
    if environment_key and blobs and not any(blob["name"] == BOOTSTRAP_KEY for blob in blobs):
        raise ValueError("environment state exists without bootstrap state; recover explicitly")


def check_recovery(
    document: Any, config: EnvironmentConfig, subscription: str, *, allow_empty: bool = False
) -> None:
    if not isinstance(document, dict) or document.get("version") != 4:
        raise ValueError("unsupported bootstrap recovery state")
    if (
        not isinstance(document.get("lineage"), str)
        or not document["lineage"]
        or not isinstance(document.get("serial"), int)
        or isinstance(document["serial"], bool)
    ):
        raise ValueError("bootstrap recovery state lacks lineage or serial")
    group_id = (
        f"/subscriptions/{UUID(subscription)}/resourceGroups/"
        f"{config.spec.terraform.state_resource_group}"
    )
    expected = {
        "azurerm_resource_group": group_id,
        "azurerm_storage_account": (
            f"{group_id}/providers/Microsoft.Storage/storageAccounts/"
            f"{config.spec.terraform.state_storage_account}"
        ),
    }
    expected["azurerm_storage_container"] = (
        f"{expected['azurerm_storage_account']}/blobServices/default/containers/{config.spec.terraform.state_container}"
    )
    resources = document.get("resources")
    if not isinstance(resources, list):
        raise ValueError("bootstrap recovery state lacks resources")
    if allow_empty and not resources:
        return
    seen: set[str] = set()
    for resource in resources:
        if not isinstance(resource, dict) or resource.get("mode") not in ("data", "managed"):
            raise ValueError("invalid bootstrap state resource shape")
        if resource.get("mode") == "data":
            continue
        kind = resource.get("type")
        if (
            not isinstance(kind, str)
            or resource.get("module")
            or kind not in {*expected, "azurerm_role_assignment"}
        ):
            raise ValueError("recovery state contains non-bootstrap resources")
        instances = resource.get("instances", [])
        if not isinstance(instances, list) or len(instances) != 1 or kind in seen:
            raise ValueError("ambiguous bootstrap state resources")
        if not isinstance(instances[0], dict):
            raise ValueError("invalid bootstrap state instance shape")
        attributes = instances[0].get("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError("invalid bootstrap state attribute shape")
        resource_id = attributes.get("id", "")
        if not isinstance(resource_id, str):
            raise ValueError("invalid bootstrap state resource ID")
        if kind in expected and resource_id.lower() != expected[kind].lower():
            raise ValueError("bootstrap state resource does not match configured backend")
        if kind == "azurerm_role_assignment" and (
            not isinstance(attributes.get("scope"), str)
            or attributes["scope"].lower() != expected["azurerm_storage_container"].lower()
        ):
            raise ValueError("bootstrap state role scope does not match the container")
        if kind == "azurerm_role_assignment":
            role_scope, separator, role_id = resource_id.lower().rpartition(
                "/providers/microsoft.authorization/roleassignments/"
            )
            if not separator or role_scope != expected["azurerm_storage_container"].lower():
                raise ValueError("bootstrap state role ID does not match the container")
            UUID(role_id)
        seen.add(kind)
    if seen != {*expected, "azurerm_role_assignment"}:
        raise ValueError("bootstrap state is incomplete")


def check_empty_environment_state(document: Any) -> None:
    if not isinstance(document, dict) or document.get("version") != 4:
        raise ValueError("environment state is not a supported object")
    if (
        not isinstance(document.get("lineage"), str)
        or not document["lineage"].strip()
        or not isinstance(document.get("serial"), int)
        or isinstance(document["serial"], bool)
        or document["serial"] < 0
        or not isinstance(document.get("outputs"), dict)
        or not isinstance(document.get("resources"), list)
    ):
        raise ValueError("environment state metadata is unverifiable")
    if document["outputs"]:
        raise ValueError("environment state is not empty; refusing removal")
    for resource in document["resources"]:
        if not isinstance(resource, dict) or resource.get("mode") != "data":
            raise ValueError("environment state is not empty; refusing removal")
        if (
            not isinstance(resource.get("type"), str)
            or not resource["type"]
            or not isinstance(resource.get("name"), str)
            or not resource["name"]
            or not isinstance(resource.get("instances"), list)
        ):
            raise ValueError("environment data-resource shape is unverifiable")
        if any(
            not isinstance(instance, dict) or not isinstance(instance.get("attributes"), dict)
            for instance in resource["instances"]
        ):
            raise ValueError("environment data-resource instances are unverifiable")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
