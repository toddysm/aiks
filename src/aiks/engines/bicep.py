"""Bicep inputs derived from the shared environment contract."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from aiks.config import EnvironmentConfig


def parameters(config: EnvironmentConfig) -> dict[str, Any]:
    """Build an ARM parameter document without engine-specific backend inputs."""

    spec = config.spec
    values = {
        "environment": spec.environment,
        "location": spec.location,
        "prefix": spec.naming.prefix,
        "adminGroupObjectId": str(spec.identity.admin_group_object_id),
        "network": spec.network.model_dump(mode="json", by_alias=True),
        "observability": spec.observability.model_dump(mode="json", by_alias=True),
        "tags": spec.tags,
    }
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in values.items()},
    }


def write_parameters(config: EnvironmentConfig, destination: Path) -> None:
    """Write validated nonsecret inputs for independently runnable native tools."""
    destination.write_text(json.dumps(parameters(config), indent=2) + "\n", encoding="utf-8")


@contextmanager
def template_path() -> Iterator[Path]:
    """Locate the checked-in modules in a source checkout or installed wheel."""
    packaged = files("aiks").joinpath("infrastructure/aks-automatic/bicep")
    if packaged.is_dir():
        with as_file(packaged) as directory:
            yield directory / "main.bicep"
    else:
        source = (
            Path(__file__).resolve().parents[3] / "infrastructure/aks-automatic/bicep/main.bicep"
        )
        if not source.is_file():
            raise FileNotFoundError("Bicep infrastructure assets are missing; reinstall aiks")
        yield source


def deployment_command(
    operation: Literal["validate", "what-if", "create"],
    *,
    config: EnvironmentConfig,
    subscription_id: str,
    template: Path,
    parameter_file: Path,
) -> tuple[str, ...]:
    """Construct a native invocation without execution or implicit subscription selection."""
    if operation not in {"validate", "what-if", "create"}:
        raise ValueError("unsupported Bicep deployment operation")
    subscription = str(UUID(subscription_id))
    return (
        "az",
        "deployment",
        "sub",
        operation,
        "--subscription",
        subscription,
        "--name",
        f"aiks-{config.spec.naming.prefix}-{config.spec.environment}",
        "--location",
        config.spec.location,
        "--template-file",
        str(template.resolve()),
        "--parameters",
        f"@{parameter_file.resolve()}",
        *(
            ("--no-pretty-print", "--result-format", "FullResourcePayloads")
            if operation == "what-if"
            else ()
        ),
        "--only-show-errors",
        "--output",
        "json",
    )


def destroy_command(
    *,
    config: EnvironmentConfig,
    subscription_id: str,
    resource_group_id: str,
    confirmed_environment: str,
    allow_production: bool = False,
) -> tuple[str, ...]:
    """Construct protected group deletion after the caller verifies Azure ownership."""
    environment = config.spec.environment
    if confirmed_environment != environment:
        raise ValueError("environment confirmation does not match")
    if environment == "production" and not allow_production:
        raise ValueError("production deletion requires an explicit override")
    subscription = str(UUID(subscription_id))
    expected = (
        rf"/subscriptions/{re.escape(subscription)}/resourceGroups/"
        rf"(rg-{re.escape(config.spec.naming.prefix)}-{environment}-[a-z0-9]{{8}})"
    )
    match = re.fullmatch(expected, resource_group_id)
    if match is None:
        raise ValueError(
            "resource group does not match the subscription and environment naming contract"
        )
    return (
        "az",
        "group",
        "delete",
        "--subscription",
        subscription,
        "--name",
        match.group(1),
        "--yes",
        "--only-show-errors",
    )
