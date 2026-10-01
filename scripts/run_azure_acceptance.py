"""Bounded controller for explicitly approved live Azure acceptance campaigns."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCENARIOS = (
    ("bicep", "dev"),
    ("bicep", "production"),
    ("terraform", "dev"),
    ("terraform", "production"),
)
CLEANUP_RESERVE_SECONDS = 3600


def amount(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("cost inputs must be decimal numbers") from error
    if not result.is_finite() or result < 0:
        raise ValueError("cost inputs must be finite and nonnegative")
    return result


def admission(environment: dict[str, str], now: float) -> dict[str, Any]:
    revision = environment["AIKS_TEST_SHA"]
    campaign = environment["AIKS_CAMPAIGN"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("a full immutable commit is required")
    if revision != environment["AIKS_APPROVED_SHA"]:
        raise ValueError("commit does not match protected-environment approval")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,39}", campaign):
        raise ValueError("campaign must be a short lowercase identifier")
    budget = amount(environment.get("AIKS_MAX_SPEND_USD", "100"))
    hours = amount(environment.get("AIKS_MAX_HOURS", "12"))
    rate = amount(environment["AIKS_MAX_HOURLY_USD"])
    retained = amount(environment["AIKS_RETAINED_COST_USD"])
    if not 0 < budget <= 100 or not 1 < hours <= 12 or rate <= 0:
        raise ValueError("authorization is limited to USD 100 and 12 hours")
    if rate * hours + retained > budget * Decimal("0.8"):
        raise ValueError("estimated cost exceeds admission limit including 20 percent headroom")
    bundle = json.loads(environment["AIKS_CONFIG_BUNDLE"])
    if not isinstance(bundle, list) or len(bundle) != len(SCENARIOS):
        raise ValueError("exactly four ordered scenario configurations are required")
    configurations = []
    for index, (engine, posture) in enumerate(SCENARIOS):
        entry = bundle[index]
        if (
            not isinstance(entry, dict)
            or set(entry) != {"engine", "config"}
            or entry["engine"] != engine
            or not isinstance(entry["config"], dict)
        ):
            raise ValueError("scenario engine/configuration is invalid")
        config = entry["config"]
        spec = config.get("spec")
        if not isinstance(spec, dict) or spec.get("environment") != posture:
            raise ValueError("scenario order or environment is invalid")
        identity = hashlib.sha256(f"{campaign}/{index}".encode()).hexdigest()[:12]
        config["metadata"] = {"name": f"acceptance-{identity}"}
        spec["naming"] = {"prefix": f"at-{identity}"}
        configurations.append(config)
    return {
        "version": 1,
        "campaign": campaign,
        "revision": revision,
        "startedAt": now,
        "testDeadline": now + float(hours * 3600) - CLEANUP_RESERVE_SECONDS,
        "cleanupDeadline": now + float(hours * 3600),
        "maxSpendUsd": str(budget),
        "maxHours": str(hours),
        "estimatedTotalUsd": str(rate * hours + retained),
        "configurations": configurations,
        "scenarios": ["pending"] * len(SCENARIOS),
    }


def remaining(manifest: dict[str, Any], now: float, *, cleanup: bool = False) -> float:
    deadline = manifest["cleanupDeadline" if cleanup else "testDeadline"]
    available = float(deadline) - now
    if available <= 0:
        raise ValueError("campaign deadline reached; no further test mutations permitted")
    return available


def upgrade_configuration(config: dict[str, Any]) -> dict[str, Any]:
    upgraded: dict[str, Any] = json.loads(json.dumps(config))
    spec = upgraded["spec"]
    workload = spec.setdefault("workload", {})
    configured = workload.get("replicas")
    replicas = (
        int(configured)
        if configured is not None
        else (2 if spec["environment"] == "production" else 1)
    )
    workload["replicas"] = replicas + 1 if replicas < 20 else replicas - 1
    return upgraded


def private_directory(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("runner storage must be an absolute private path")
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError("runner storage must not contain symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("runner storage must be owner-only")
    return path


def save(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def read(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("campaign artifact must be an owner-only regular file")
    return json.loads(path.read_text())


@contextmanager
def campaign_lock(directory: Path) -> Iterator[None]:
    import fcntl

    descriptor = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "campaign is active; cleanup requested for the running controller"
            ) from error
        yield


def execute(
    arguments: list[str],
    directory: Path,
    timeout: float,
    stop_file: Path | None,
    answer: str = "",
) -> None:
    if timeout <= 0 or (stop_file is not None and stop_file.exists()):
        raise TimeoutError("operation refused before process creation")
    deadline = time.monotonic() + min(timeout, 2400)
    with (
        tempfile.NamedTemporaryFile(
            dir=directory, prefix="command-", suffix=".log", delete=False
        ) as output,
        subprocess.Popen(
            arguments,
            cwd=directory,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=output,
            start_new_session=True,
        ) as process,
    ):
        try:
            first = True
            while True:
                available = deadline - time.monotonic()
                if available <= 0 or (stop_file is not None and stop_file.exists()):
                    raise TimeoutError("operation stopped by campaign deadline or cleanup request")
                try:
                    process.communicate(
                        input=answer.encode() if first else None,
                        timeout=min(10, available),
                    )
                    break
                except subprocess.TimeoutExpired:
                    first = False
            if process.returncode:
                raise RuntimeError("acceptance command failed; private result contains details")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)


class Campaign:
    def __init__(self, environment: dict[str, str]) -> None:
        self.environment = environment
        self.candidate = admission(environment, time.time())
        root = private_directory(Path(environment["AIKS_RUN_ROOT"]))
        self.directory = private_directory(root / self.candidate["campaign"])
        self.manifest_path = self.directory / "campaign.json"
        self.stop_file = self.directory / "stop.json"
        self.manifest = self.candidate

    def load(self, *, create: bool = False) -> None:
        if self.manifest_path.exists():
            self.manifest = read(self.manifest_path)
            for key in (
                "campaign",
                "revision",
                "configurations",
                "maxSpendUsd",
                "maxHours",
                "estimatedTotalUsd",
            ):
                if self.manifest.get(key) != self.candidate[key]:
                    raise ValueError(
                        "protected settings changed; restore the original campaign settings"
                    )
        elif create:
            save(self.manifest_path, self.manifest)
            for index, config in enumerate(self.manifest["configurations"]):
                directory = private_directory(self.directory / str(index))
                save(directory / "environment.json", config)
        else:
            raise ValueError("no campaign receipt exists; no resources will be adopted")

    def cli(
        self,
        index: int,
        group: str,
        operation: str,
        *,
        cleanup: bool = False,
        extra: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        engine, posture = SCENARIOS[index]
        directory = self.directory / str(index)
        result = directory / f"{group}-{operation}.json"
        arguments = [
            self.environment["AIKS_PYTHON"],
            "-m",
            "aiks.cli",
            group,
            operation,
            "--config",
            str(directory / "environment.json"),
            "--json-output",
            str(result),
        ]
        if group == "infra":
            arguments += ["--engine", engine]
            if posture == "production" and operation in {"deploy", "destroy", "exercise-alerts"}:
                suffix = operation if operation != "exercise-alerts" else "change"
                arguments += [f"--allow-production-{suffix}"]
        elif group == "workload" and operation == "build":
            arguments += ["--target", "kind"]
        arguments += list(extra)
        timeout = 1800 if cleanup else remaining(self.manifest, time.time())
        execute(arguments, directory, timeout, None if cleanup else self.stop_file, posture + "\n")
        value = read(result)
        if not isinstance(value, dict) or value.get("succeeded") is not True:
            raise RuntimeError("operation did not produce successful structured evidence")
        context = value.get("context")
        if not isinstance(context, dict):
            raise ValueError("operation evidence is missing its context")
        return context

    def cleanup(self, index: int) -> None:
        state = self.manifest["scenarios"][index]
        if state in {"pending", "passed", "cleaned"}:
            return
        directory = self.directory / str(index)
        receipts = list((directory / ".aiks" / "infra").glob("*/owner.json"))
        intent = list((directory / ".aiks" / "infra").glob("*/intent.json"))
        if not receipts and not intent:
            if state == "preflight":
                self.manifest["scenarios"][index] = "cleaned"
                save(self.manifest_path, self.manifest)
                return
            raise ValueError("deployment interrupted before receipt; manual inspection required")
        extra = () if receipts else ("--allow-partial-cleanup",)
        self.cli(index, "infra", "destroy", cleanup=True, extra=extra)
        self.manifest["scenarios"][index] = "cleaned"
        save(self.manifest_path, self.manifest)

    def workload(self, index: int, operation: str) -> None:
        directory = self.directory / str(index)
        outputs = list((directory / ".aiks" / "infra").glob("*/outputs.json"))
        if len(outputs) != 1:
            raise ValueError("one verified foundation output is required")
        foundation = read(outputs[0])
        self.cli(
            index,
            "workload",
            operation,
            extra=(
                "--target",
                "aks",
                "--outputs",
                str(outputs[0]),
                "--kubeconfig",
                str(outputs[0].parent / "kubeconfig"),
                "--context",
                foundation["cluster"]["name"],
                "--allow-production-change",
            ),
        )

    def run(self, index: int) -> None:
        with campaign_lock(self.directory):
            self.load(create=index == 0)
            if self.manifest["scenarios"][index] == "passed":
                return
            if self.stop_file.exists() or self.manifest["scenarios"][index] != "pending":
                raise ValueError(
                    "campaign stopped or scenario already attempted; recover before retry"
                )
            if any(state != "passed" for state in self.manifest["scenarios"][:index]):
                raise ValueError("previous scenarios must pass before another deployment")
            success = False
            self.manifest["scenarios"][index] = "preflight"
            save(self.manifest_path, self.manifest)
            try:
                self.cli(index, "infra", "preflight")
                self.cli(index, "workload", "build")
                self.cli(index, "infra", "plan")
                self.manifest["scenarios"][index] = "deploying"
                save(self.manifest_path, self.manifest)
                self.cli(index, "infra", "deploy")
                self.cli(index, "infra", "verify")
                if self.cli(index, "infra", "plan").get("noOp") is not True:
                    raise ValueError("repeat preview is not a verified no-op")
                self.cli(index, "infra", "deploy")
                config_path = self.directory / str(index) / "environment.json"
                original = read(config_path)
                upgraded = upgrade_configuration(original)
                try:
                    save(config_path, upgraded)
                    self.workload(index, "upgrade")
                finally:
                    save(config_path, original)
                self.workload(index, "rollback")
                self.workload(index, "verify")
                if SCENARIOS[index][1] == "production":
                    self.cli(index, "infra", "exercise-alerts")
                success = True
            finally:
                if not success:
                    save(self.stop_file, {"reason": "scenario failed"})
                try:
                    self.cleanup(index)
                except (ValueError, OSError, RuntimeError, TimeoutError):
                    save(self.stop_file, {"reason": "cleanup needs recovery"})
                    raise
                if success:
                    self.manifest["scenarios"][index] = "passed"
                    save(self.manifest_path, self.manifest)

    def recover(self, *, expired_only: bool) -> None:
        self.load()
        if (
            expired_only
            and time.time() < self.manifest["testDeadline"]
            and not self.stop_file.exists()
        ):
            return
        save(self.stop_file, {"reason": "independent cleanup requested"})
        with campaign_lock(self.directory):
            self.load()
            for index in range(len(SCENARIOS)):
                self.cleanup(index)

    def summary(self) -> None:
        if self.manifest_path.exists():
            self.manifest = read(self.manifest_path)
        summary = {
            "revision": self.manifest["revision"],
            "scenarios": self.manifest["scenarios"],
            "automatedLifecyclePassed": all(
                value == "passed" for value in self.manifest["scenarios"]
            ),
            "fullAcceptancePassed": False,
            "manualEvidenceRequired": [
                "notification delivery",
                "backend recovery",
                "negative drift fixtures",
            ],
            "actualCostVerified": False,
        }
        destination = Path(self.environment["RUNNER_TEMP"]) / "acceptance-summary.json"
        save(destination, summary)
        print(json.dumps(summary))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scenario", type=int, choices=range(4))
    mode.add_argument("--cleanup", action="store_true")
    parser.add_argument("--expired-only", action="store_true")
    arguments = parser.parse_args()
    campaign = None
    try:
        campaign = Campaign(dict(os.environ))
        if arguments.cleanup:
            campaign.recover(expired_only=arguments.expired_only)
        else:
            campaign.run(arguments.scenario)
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, TimeoutError):
        print("Acceptance refused or failed; inspect the private campaign and setup instructions.")
        return 1
    finally:
        if campaign is not None:
            campaign.summary()


if __name__ == "__main__":
    sys.exit(main())
