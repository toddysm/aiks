"""Validate tracked structured data and credential-free workflow policy."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml
from jsonschema.validators import validator_for


def unique_pairs(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate structured-data key")
        result[key] = value
    return result


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    return unique_pairs(
        [
            (loader.construct_object(key), loader.construct_object(value))
            for key, value in node.value
        ]
    )


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def workflow_policy(document: dict[str, Any]) -> None:
    supported_actions = json.loads(
        (Path(__file__).resolve().parents[1] / ".github/action-policy.json").read_text()
    )
    if document.get("permissions") != {"contents": "read"}:
        raise ValueError("workflow needs explicit read-only default permissions")
    if not document.get("concurrency"):
        raise ValueError("workflow needs concurrency cancellation")
    triggers = document.get("on", document.get(True, {}))
    if "pull_request_target" in triggers:
        raise ValueError("privileged pull request execution is prohibited")
    for name, job in document["jobs"].items():
        if "uses" in job:
            raise ValueError("reusable workflows require an explicit reviewed policy")
        permissions = job.get("permissions", {})
        allowed = {
            "contents": "read",
            "actions": "read",
            "packages": "read",
            "security-events": "write",
        }
        if any(
            allowed.get(key) != value or (value == "write" and name != "analyze")
            for key, value in permissions.items()
        ):
            raise ValueError("unexpected job permission")
        for step in job.get("steps", []):
            action = step.get("uses")
            if action and not re.fullmatch(
                r"[A-Za-z0-9_./-]+@(?:v\d+(?:\.\d+){0,2}|[0-9a-f]{40})", action
            ):
                raise ValueError("action must pin a supported release or commit")
            if action and action.lower().startswith("azure/login@"):
                raise ValueError("static validation must not authenticate to Azure")
            if action:
                action_name, version = action.rsplit("@", 1)
                if action_name not in supported_actions or (
                    not re.fullmatch(r"[0-9a-f]{40}", version)
                    and version.split(".")[0] not in supported_actions[action_name]
                ):
                    raise ValueError("action owner/name or release major is not supported")
            command = step.get("run", "")
            if re.search(
                r"\b(?:az|azd)(?:\s|$)|\b[A-Za-z]+-Az[A-Za-z]+\b|\bterraform\s+(?:-chdir=\S+\s+)?(?:apply|destroy)\b",
                command,
            ):
                raise ValueError("static workflow must not execute Azure commands or deployments")


def validate_file(path: Path) -> None:
    if path.suffix == ".json":
        document = json.loads(path.read_text(), object_pairs_hook=unique_pairs)
        if path.name.endswith("schema.json"):
            validator_for(document).check_schema(document)
    else:
        documents = list(yaml.load_all(path.read_text(), Loader=UniqueLoader))
        if ".github/workflows" in path.as_posix():
            if len(documents) != 1 or not isinstance(documents[0], dict):
                raise ValueError("workflow must contain one mapping")
            workflow_policy(documents[0])


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=30,
        )
        .stdout.decode()
        .split("\0")
    )
    failures = []
    checked = 0
    for relative in paths:
        path = root / relative
        if path.suffix not in {".json", ".yaml", ".yml"}:
            continue
        if relative.startswith("infrastructure/aks-automatic/charts/readiness/templates/"):
            continue
        try:
            validate_file(path)
            checked += 1
        except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
            failures.append(f"{relative}: {type(error).__name__}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Validated {checked} structured files and workflow policies")


if __name__ == "__main__":
    main()
