"""Validate every rendered chart object against standard or pinned custom schemas."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "infrastructure/aks-automatic/charts/readiness"
MONITOR_SCHEMA = (
    "https://raw.githubusercontent.com/Azure/prometheus-collector/"
    "179fc75054e59f40b28d6bcc464be65c7df92e2d/otelcollector/deploy/"
    "addon-chart/azure-monitor-metrics-addon/templates/ama-metrics-servicemonitor-crd.yaml"
)


def main() -> None:
    result = subprocess.run(
        ["helm", "show", "crds", "oci://docker.io/envoyproxy/gateway-helm", "--version", "v1.9.1"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    definitions = [
        item
        for item in yaml.safe_load_all(result.stdout)
        if isinstance(item, dict) and item.get("kind") == "CustomResourceDefinition"
    ]
    response = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--max-time",
            "60",
            MONITOR_SCHEMA,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=65,
    )
    definitions.append(yaml.safe_load(response.stdout))
    schemas = {
        (
            f"{definition['spec']['group']}/{version['name']}",
            definition["spec"]["names"]["kind"],
        ): version["schema"]["openAPIV3Schema"]
        for definition in definitions
        for version in definition["spec"]["versions"]
        if "schema" in version
    }
    for profile in ("kind", "aks-dev", "aks-production"):
        arguments = [
            "helm",
            "template",
            "aiks-readiness",
            str(CHART),
            "-f",
            str(CHART / f"values-{profile}.yaml"),
        ]
        if profile != "kind":
            arguments += [
                "--set",
                "image.repository=test.azurecr.io/readiness",
                "--set",
                "image.digest=sha256:" + "a" * 64,
                "--set",
                "identity.clientId=11111111-1111-4111-8111-111111111111",
                "--set",
                "identity.tenantId=11111111-1111-4111-8111-111111111111",
                "--set",
                "identity.vaultUri=https://example.vault.azure.net",
            ]
        rendered = subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=30)
        documents = [item for item in yaml.safe_load_all(rendered.stdout) if item]
        standard = []
        custom_count = 0
        for document in documents:
            schema = schemas.get((document["apiVersion"], document["kind"]))
            if schema:
                Draft7Validator(schema).validate(document)
                custom_count += 1
            else:
                standard.append(document)
        subprocess.run(
            ["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.35.0"],
            input="\n---\n".join(json.dumps(document) for document in standard),
            text=True,
            check=True,
            timeout=120,
        )
        print(
            f"{profile}: {len(standard)} standard objects, {custom_count} custom objects validated"
        )


if __name__ == "__main__":
    main()
