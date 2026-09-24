"""Typed, secret-free environment configuration."""

from __future__ import annotations

import json
import re
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from pathlib import Path
from typing import Any, Literal, Self, cast
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EnvironmentName = Literal["dev", "production"]

_AZURE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
_STORAGE_NAME = re.compile(r"^[a-z0-9]{3,24}$")
_FORBIDDEN_KEYS = re.compile(
    r"(?:password|secret|token|api.?key|access.?key|client.?secret|client.?key.?data|"
    r"client.?certificate.?data|private.?key|credential|"
    r"connection.?string|storage.?key|shared.?access.?key|sas.?token|kubeconfig)",
    re.IGNORECASE,
)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.capitalize() for word in rest)


class StrictModel(BaseModel):
    """Base model with camelCase aliases and no undeclared configuration."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Metadata(StrictModel):
    """Configuration metadata."""

    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$")


class Naming(StrictModel):
    """Azure resource naming inputs."""

    prefix: str

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not _AZURE_NAME.fullmatch(value):
            raise ValueError("prefix must be 3-24 lowercase letters, numbers, or hyphens")
        return value


class Identity(StrictModel):
    """Nonsecret Microsoft Entra identity inputs."""

    admin_group_object_id: UUID


class Network(StrictModel):
    """Day-0 network configuration."""

    vnet_cidr: str
    api_server_subnet_cidr: str
    system_node_subnet_cidr: str
    user_node_subnet_cidr: str
    private_endpoint_subnet_cidr: str
    pod_cidr: str
    service_cidr: str
    dns_service_ip: str
    private_cluster: bool
    authorized_ip_ranges: list[str] = Field(default_factory=list)
    paas_allowed_ip_ranges: list[str] = Field(default_factory=list)

    @field_validator(
        "vnet_cidr",
        "api_server_subnet_cidr",
        "system_node_subnet_cidr",
        "user_node_subnet_cidr",
        "private_endpoint_subnet_cidr",
        "pod_cidr",
        "service_cidr",
    )
    @classmethod
    def validate_network(cls, value: str) -> str:
        network = ip_network(value, strict=True)
        if not isinstance(network, IPv4Network):
            raise ValueError("only IPv4 networks are supported")
        return str(network)

    @field_validator("authorized_ip_ranges", "paas_allowed_ip_ranges")
    @classmethod
    def validate_allowed_ranges(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            network = ip_network(value, strict=True)
            if not isinstance(network, IPv4Network):
                raise ValueError("only IPv4 allowlist ranges are supported")
            if network.prefixlen == 0:
                raise ValueError("unrestricted 0.0.0.0/0 access is prohibited")
            normalized.append(str(network))
        return normalized

    @field_validator("dns_service_ip")
    @classmethod
    def validate_dns_ip(cls, value: str) -> str:
        address = ip_address(value)
        if not isinstance(address, IPv4Address):
            raise ValueError("only an IPv4 DNS service address is supported")
        return str(address)

    @model_validator(mode="after")
    def validate_topology(self) -> Self:
        vnet = IPv4Network(self.vnet_cidr)
        subnets = {
            "apiServerSubnetCidr": IPv4Network(self.api_server_subnet_cidr),
            "systemNodeSubnetCidr": IPv4Network(self.system_node_subnet_cidr),
            "userNodeSubnetCidr": IPv4Network(self.user_node_subnet_cidr),
            "privateEndpointSubnetCidr": IPv4Network(self.private_endpoint_subnet_cidr),
        }
        for name, subnet in subnets.items():
            if not subnet.subnet_of(vnet):
                raise ValueError(f"{name} must be contained by vnetCidr")

        subnet_items = list(subnets.items())
        for index, (left_name, left) in enumerate(subnet_items):
            for right_name, right in subnet_items[index + 1 :]:
                if left.overlaps(right):
                    raise ValueError(f"{left_name} overlaps {right_name}")

        pod = IPv4Network(self.pod_cidr)
        service = IPv4Network(self.service_cidr)
        if pod.overlaps(vnet) or service.overlaps(vnet) or pod.overlaps(service):
            raise ValueError("vnetCidr, podCidr, and serviceCidr must not overlap")

        dns = IPv4Address(self.dns_service_ip)
        if dns not in service or dns in {service.network_address, service.broadcast_address}:
            raise ValueError("dnsServiceIp must be a usable address inside serviceCidr")

        if not self.private_cluster and not self.authorized_ip_ranges:
            raise ValueError("public API access requires authorizedIpRanges")
        return self


class Observability(StrictModel):
    """Environment monitoring controls."""

    container_insights: bool = True
    managed_prometheus: bool = False
    managed_grafana: bool = False
    log_retention_days: int = Field(default=30, ge=30, le=730)
    action_group_resource_ids: list[str] = Field(default_factory=list)
    action_group_receivers: list[ActionGroupReceiver] = Field(default_factory=list)


class ActionGroupReceiver(StrictModel):
    """Nonsecret email receiver used to create an environment action group."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    email_address: str = Field(
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )


class TerraformState(StrictModel):
    """Terraform backend names; authentication always comes from Microsoft Entra ID."""

    state_resource_group: str
    state_storage_account: str
    state_container: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9-]+$")
    allowed_ip_ranges: list[str] = Field(default_factory=list)
    allowed_subnet_ids: list[str] = Field(default_factory=list)

    @field_validator("allowed_ip_ranges")
    @classmethod
    def validate_state_ranges(cls, values: list[str]) -> list[str]:
        return Network.validate_allowed_ranges(values)

    @field_validator("allowed_subnet_ids")
    @classmethod
    def validate_state_subnets(cls, values: list[str]) -> list[str]:
        for value in values:
            if not re.fullmatch(
                r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/"
                r"Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+",
                value,
            ):
                raise ValueError("allowedSubnetIds must contain full Azure subnet resource IDs")
        return values

    @field_validator("state_container")
    @classmethod
    def validate_container(cls, value: str) -> str:
        if value.startswith("-") or value.endswith("-") or "--" in value:
            raise ValueError("stateContainer must not have leading, trailing, or repeated hyphens")
        return value

    @field_validator("state_resource_group")
    @classmethod
    def validate_resource_group(cls, value: str) -> str:
        if not _AZURE_NAME.fullmatch(value):
            raise ValueError("stateResourceGroup must use lowercase Azure-safe naming")
        return value

    @field_validator("state_storage_account")
    @classmethod
    def validate_storage_account(cls, value: str) -> str:
        if not _STORAGE_NAME.fullmatch(value):
            raise ValueError("stateStorageAccount must be 3-24 lowercase letters or numbers")
        return value


class LocalKubernetes(StrictModel):
    """Local kind target configuration."""

    kind_cluster_name: str = Field(
        default="aiks-readiness", min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]*$"
    )
    node_image: str = (
        "kindest/node:v1.35.8@sha256:"
        "07b2536e30b803ed61d1677a79df6115f798ce64c80f9e22f6ed45afd09323c0"
    )
    gateway_chart_version: str = Field(default="v1.9.1", pattern=r"^v\d+\.\d+\.\d+$")


class ReadinessWorkload(StrictModel):
    """Nonsecret workload and local image-build settings."""

    image: str = Field(default="aiks-readiness:local", pattern=r"^[a-z0-9][a-zA-Z0-9./:@_-]*$")
    replicas: int | None = Field(default=None, ge=1, le=20)
    ready: bool = True
    timeout_seconds: int = Field(default=300, ge=30, le=1800)
    package_index_url: str = "https://pypi.org/simple"

    @field_validator("package_index_url")
    @classmethod
    def validate_package_index(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("packageIndexUrl must be an HTTPS index without credentials or query")
        return value


class EnvironmentSpec(StrictModel):
    """Complete environment specification."""

    environment: EnvironmentName
    location: str = Field(default="westus3", pattern=r"^[a-z0-9]+$")
    naming: Naming
    identity: Identity
    network: Network
    observability: Observability
    terraform: TerraformState
    local: LocalKubernetes = Field(default_factory=LocalKubernetes)
    workload: ReadinessWorkload = Field(default_factory=ReadinessWorkload)
    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_environment_posture(self) -> Self:
        if self.environment == "production":
            if not self.network.private_cluster:
                raise ValueError("production requires a private AKS API")
            if not self.observability.container_insights:
                raise ValueError("production requires Container Insights")
            if not self.observability.managed_prometheus:
                raise ValueError("production requires managed Prometheus")
            if not self.observability.managed_grafana:
                raise ValueError("production requires managed Grafana")
            if not (
                self.observability.action_group_resource_ids
                or self.observability.action_group_receivers
            ):
                raise ValueError("production requires an action group or receiver")
        elif not self.network.paas_allowed_ip_ranges:
            raise ValueError("dev requires PaaS operator allowlist ranges")
        return self


class EnvironmentConfig(StrictModel):
    """Versioned root configuration document."""

    api_version: Literal["aiks.io/v1alpha1"]
    kind: Literal["AksAutomaticEnvironment"]
    metadata: Metadata
    spec: EnvironmentSpec

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        if self.metadata.name != self.spec.environment:
            raise ValueError("metadata.name must match spec.environment")
        return self


def _find_forbidden_key(value: Any, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = (*path, key_text)
            if _FORBIDDEN_KEYS.search(key_text):
                return ".".join(child_path)
            found = _find_forbidden_key(child, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden_key(child, (*path, str(index)))
            if found:
                return found
    return None


def load_environment_config(path: Path) -> EnvironmentConfig:
    """Load and validate a YAML environment file without accepting secret-shaped fields."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"unable to load configuration {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a YAML mapping")
    forbidden = _find_forbidden_key(raw)
    if forbidden:
        raise ValueError(f"secret-shaped configuration field is prohibited: {forbidden}")
    return EnvironmentConfig.model_validate(cast(dict[str, Any], raw))


def schema_json() -> str:
    """Return the deterministic JSON Schema used by editors and CI."""

    schema = EnvironmentConfig.model_json_schema(by_alias=True, mode="validation")
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write_schema(path: Path) -> None:
    """Write the current configuration schema."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_json(), encoding="utf-8")
