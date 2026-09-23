"""Shared nonsecret foundation output contract for infrastructure engine parity."""

from aiks.config import EnvironmentName, StrictModel


class ResourceInfo(StrictModel):
    name: str
    id: str


class ClusterInfo(ResourceInfo):
    fqdn: str


class RegistryInfo(ResourceInfo):
    login_server: str


class VaultInfo(ResourceInfo):
    uri: str
    marker_key_name: str


class IdentityInfo(ResourceInfo):
    client_id: str
    principal_id: str


class Identities(StrictModel):
    cluster: IdentityInfo
    readiness: IdentityInfo


class SubnetIds(StrictModel):
    api_server: str
    system_node: str
    user_node: str
    private_endpoint: str


class NetworkInfo(StrictModel):
    vnet_id: str
    subnet_ids: SubnetIds


class MonitoringInfo(StrictModel):
    log_analytics_id: str
    azure_monitor_workspace_id: str
    prometheus_query_endpoint: str
    grafana_id: str
    grafana_endpoint: str


class ReadinessInfo(StrictModel):
    namespace: str
    service_account: str
    client_id: str
    tenant_id: str
    vault_uri: str
    marker_key_name: str
    gateway_class_name: str
    internal_gateway: bool
    managed_prometheus: bool


class FoundationOutputs(StrictModel):
    environment: EnvironmentName
    location: str
    resource_group: ResourceInfo
    cluster: ClusterInfo
    registry: RegistryInfo
    vault: VaultInfo
    network: NetworkInfo
    identities: Identities
    monitoring: MonitoringInfo
    readiness: ReadinessInfo
