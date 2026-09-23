from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from aiks.config import load_environment_config, schema_json

ROOT = Path(__file__).parents[1]
CONFIG_DIR = ROOT / "infrastructure" / "aks-automatic" / "config"


@pytest.mark.parametrize("name", ["dev.example.yaml", "production.example.yaml"])
def test_example_configuration_is_valid(name: str) -> None:
    config = load_environment_config(CONFIG_DIR / name)

    assert config.spec.location == "westus3"
    assert str(config.spec.identity.admin_group_object_id)


def test_committed_schema_matches_model() -> None:
    committed = json.loads((CONFIG_DIR / "schema.json").read_text())

    assert committed == json.loads(schema_json())


def test_rejects_overlapping_subnets(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "dev.example.yaml").read_text())
    raw["spec"]["network"]["systemNodeSubnetCidr"] = "10.20.1.0/25"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValidationError, match="overlaps"):
        load_environment_config(path)


@pytest.mark.parametrize(
    "key", ["clientSecret", "apiKey", "accessKey", "storageKey", "client-key-data"]
)
def test_rejects_secret_shaped_field(tmp_path: Path, key: str) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "dev.example.yaml").read_text())
    raw["spec"][key] = "not-a-real-secret"
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="secret-shaped"):
        load_environment_config(path)


def test_production_requires_private_cluster_and_monitoring(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "production.example.yaml").read_text())
    raw["spec"]["network"]["privateCluster"] = False
    raw["spec"]["network"]["authorizedIpRanges"] = ["203.0.113.10/32"]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValidationError, match="production requires a private AKS API"):
        load_environment_config(path)


def test_production_requires_action_group_or_receiver(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "production.example.yaml").read_text())
    raw["spec"]["observability"]["actionGroupResourceIds"] = []
    raw["spec"]["observability"]["actionGroupReceivers"] = []
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValidationError, match="action group or receiver"):
        load_environment_config(path)


def test_dev_requires_restricted_paas_access(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "dev.example.yaml").read_text())
    raw["spec"]["network"]["paasAllowedIpRanges"] = []
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValidationError, match="PaaS operator allowlist"):
        load_environment_config(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["spec"]["network"].update(vnetCidr="10.20.0.1/16"), "host bits"),
        (
            lambda raw: raw["spec"]["network"].update(apiServerSubnetCidr="10.21.0.0/28"),
            "contained by vnetCidr",
        ),
        (lambda raw: raw["spec"]["network"].update(podCidr="10.20.4.0/24"), "must not overlap"),
        (lambda raw: raw["spec"]["network"].update(dnsServiceIp="10.2.0.10"), "usable address"),
        (
            lambda raw: raw["spec"]["network"].update(authorizedIpRanges=["0.0.0.0/0"]),
            "unrestricted",
        ),
        (lambda raw: raw["metadata"].update(name="production"), "must match"),
        (lambda raw: raw["spec"]["naming"].update(prefix="BAD"), "prefix"),
        (
            lambda raw: raw["spec"]["terraform"].update(stateStorageAccount="invalid-name"),
            "stateStorageAccount",
        ),
    ],
)
def test_rejects_invalid_configuration(tmp_path: Path, mutate: object, message: str) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "dev.example.yaml").read_text())
    assert callable(mutate)
    mutate(raw)
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises((ValueError, ValidationError), match=message):
        load_environment_config(path)


def test_rejects_non_mapping_document(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("- not\n- a\n- mapping\n")

    with pytest.raises(ValueError, match="root must be a YAML mapping"):
        load_environment_config(path)
