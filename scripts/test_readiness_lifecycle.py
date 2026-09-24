"""Run the complete readiness lifecycle on a uniquely owned local kind cluster."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import yaml

from aiks.config import ReadinessWorkload, load_environment_config


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    config = load_environment_config(root / "infrastructure/aks-automatic/config/dev.example.yaml")
    name = "aiks-test-9-" + uuid4().hex[:8]
    config.spec.local.kind_cluster_name = name
    config.spec.workload.timeout_seconds = 600
    config.spec.workload.image = f"aiks-readiness:{name}"
    config.spec.workload.package_index_url = ReadinessWorkload.validate_package_index(
        os.environ.get("AIKS_TEST_PACKAGE_INDEX", "https://pypi.org/simple")
    )
    directory = root / ".aiks" / "integration" / name
    directory.mkdir(mode=0o700, parents=True)
    path = directory / "environment.yaml"

    def save() -> None:
        path.write_text(yaml.safe_dump(config.model_dump(mode="json", by_alias=True)))
        path.chmod(0o600)

    def command(group: str, operation: str, answer: str | None = None) -> None:
        arguments = [sys.executable, "-m", "aiks.cli", group, operation, "--config", str(path)]
        if group == "workload":
            arguments += ["--target", "kind"]
        arguments += ["--json-output", str(directory / f"{group}-{operation}.json")]
        subprocess.run(arguments, input=answer, text=True, check=True, timeout=900)

    save()
    existing = subprocess.check_output(["kind", "get", "clusters"], text=True).split()
    if name in existing:
        raise RuntimeError("test cluster name unexpectedly exists")
    try:
        command("workload", "build")
        command("local", "create")
        command("workload", "install")
        command("workload", "verify")
        config.spec.workload.replicas = 2
        save()
        command("workload", "upgrade")
        command("workload", "rollback")
        kubeconfig = root / ".aiks" / "local" / name / "kubeconfig"
        result = subprocess.check_output(
            [
                "kubectl",
                "--kubeconfig",
                str(kubeconfig),
                "--context",
                f"kind-{name}",
                "get",
                "deployment/readiness",
                "-n",
                "aiks-readiness",
                "-o",
                "json",
            ],
            text=True,
        )
        if json.loads(result)["spec"]["replicas"] != 1:
            raise RuntimeError("rollback did not restore the prior replica count")
        command("workload", "uninstall", "aiks-readiness\n")
        command("local", "delete", name + "\n")
        print("Local lifecycle passed, including rollback, uninstall, and cluster deletion")
    finally:
        remaining = subprocess.check_output(["kind", "get", "clusters"], text=True).split()
        if name in remaining:
            subprocess.run(["kind", "delete", "cluster", "--name", name], check=True, timeout=180)


if __name__ == "__main__":
    main()
