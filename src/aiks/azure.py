"""Explicit Azure CLI context and fail-closed environment ownership checks."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from uuid import UUID

from aiks.config import EnvironmentConfig
from aiks.engines.bicep import destroy_command
from aiks.process import run_command


def cli_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(
            (
                "ARM_",
                "TF_",
                "AZURE_STORAGE_",
                "AZURE_CLIENT_",
                "AZURE_TENANT_",
                "AZURE_FEDERATED_",
            )
        )
    }
    if "PATH" in environment:
        environment["PATH"] = os.pathsep.join(
            os.path.abspath(os.path.expanduser(part or "."))
            for part in environment["PATH"].split(os.pathsep)
        )
    for name in ("AZURE_CONFIG_DIR", "AZURE_EXTENSION_DIR", "DOCKER_CONFIG", "HOME"):
        if name in environment:
            environment[name] = os.path.abspath(os.path.expanduser(environment[name] or "."))
    return environment


def object_response(value: Any, category: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{category} response is not an object")
    return value


class AzureSession:
    def __init__(self) -> None:
        self.environment = cli_environment()
        self.subscription = ""
        account = self.json("account", "show")
        if not isinstance(account, dict) or account.get("state") != "Enabled":
            raise ValueError("an enabled Azure CLI account is required")
        try:
            self.subscription = str(UUID(account["id"]))
            self.tenant = str(UUID(account["tenantId"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Azure CLI account context is invalid") from error
        self.environment.update(
            ARM_USE_CLI="true",
            ARM_USE_MSI="false",
            ARM_USE_OIDC="false",
            ARM_SUBSCRIPTION_ID=self.subscription,
            ARM_TENANT_ID=self.tenant,
            TF_IN_AUTOMATION="1",
        )

    def json(self, *arguments: str, timeout: float = 120) -> Any:
        command = ["az", *arguments, "--only-show-errors", "--output", "json"]
        if self.subscription:
            command += ["--subscription", self.subscription]
        result = run_command(command, environment=self.environment, timeout_seconds=timeout)
        if not result.succeeded:
            raise ValueError(f"Azure {arguments[0]} operation failed (exit {result.return_code})")
        try:
            return json.loads(result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError as error:
            raise ValueError("Azure returned an invalid JSON response") from error


def require_owned_group(group: Any, config: EnvironmentConfig, subscription: str) -> str:
    """Validate identity and ownership independently of caller-supplied output files."""
    if not isinstance(group, dict):
        raise ValueError("resource-group ownership could not be verified")
    resource_id, name, tags = group.get("id"), group.get("name"), group.get("tags")
    if not isinstance(resource_id, str) or not isinstance(name, str) or not isinstance(tags, dict):
        raise ValueError("resource-group ownership metadata is malformed")
    expected = rf"rg-{re.escape(config.spec.naming.prefix)}-{config.spec.environment}-[a-z0-9]{{8}}"
    if not re.fullmatch(expected, name):
        raise ValueError("resource group does not match the configured environment")
    if name.lower() == config.spec.terraform.state_resource_group.lower():
        raise ValueError("environment operations must never target the Terraform backend")
    if tags.get("aiks-managed") != "true" or tags.get("environment") != config.spec.environment:
        raise ValueError("resource group is not owned by this environment")
    if tags.get("aiks-purpose") == "terraform-state":
        raise ValueError("environment operations must never target the Terraform backend")
    destroy_command(
        config=config,
        subscription_id=subscription,
        resource_group_id=resource_id,
        confirmed_environment=config.spec.environment,
        allow_production=True,
    )
    if resource_id.rsplit("/", 1)[-1] != name:
        raise ValueError("resource-group name and resource ID disagree")
    return name
