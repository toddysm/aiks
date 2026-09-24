"""Negative fixtures for the repository's static gates."""

import copy
import runpy
from pathlib import Path

import pytest

CHECKS = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_repository.py"))


@pytest.mark.parametrize(
    "suffix,content", [(".json", '{"key":1,"key":2}'), (".yaml", "key: 1\nkey: 2\n")]
)
def test_duplicate_keys_fail(tmp_path, suffix, content):
    path = tmp_path / f"invalid{suffix}"
    path.write_text(content)
    with pytest.raises(ValueError, match="duplicate"):
        CHECKS["validate_file"](path)


@pytest.mark.parametrize(
    "mutation", ["write", "concurrency", "privileged", "action", "login", "apply", "major", "owner"]
)
def test_unsafe_workflow_fails(mutation):
    workflow = {
        "on": {"pull_request": {}},
        "permissions": {"contents": "read"},
        "concurrency": {"group": "test", "cancel-in-progress": True},
        "jobs": {"test": {"steps": [{"uses": "actions/checkout@v7"}]}},
    }
    CHECKS["workflow_policy"](copy.deepcopy(workflow))
    if mutation == "write":
        workflow["permissions"] = {"contents": "write"}
    elif mutation == "concurrency":
        del workflow["concurrency"]
    elif mutation == "privileged":
        workflow["on"] = {"pull_request_target": {}}
    elif mutation == "action":
        workflow["jobs"]["test"]["steps"] = [{"uses": "actions/checkout@main"}]
    elif mutation == "login":
        workflow["jobs"]["test"]["steps"] = [{"uses": "azure/login@v2"}]
    elif mutation == "major":
        workflow["jobs"]["test"]["steps"] = [{"uses": "actions/checkout@v999"}]
    elif mutation == "owner":
        workflow["jobs"]["test"]["steps"] = [{"uses": "unrecognized/action@v1"}]
    else:
        workflow["jobs"]["test"]["steps"] = [{"run": "terraform -chdir=example apply"}]
    with pytest.raises(ValueError):
        CHECKS["workflow_policy"](workflow)


@pytest.mark.parametrize(
    "job",
    [
        {"uses": "owner/repo/.github/workflows/check.yml@main"},
        {"steps": [{"uses": "azure/cli@v2"}]},
        {"steps": [{"uses": "azure/azure-powershell@v2"}]},
        {"steps": [{"run": "az group create --name test --location westus3"}]},
        {"steps": [{"run": "az resource create --name test"}]},
        {"steps": [{"run": "azd up"}]},
        {"steps": [{"run": "New-AzResourceGroup -Name test"}]},
    ],
)
def test_reusable_and_cloud_execution_are_rejected(job):
    workflow = {
        "on": {"pull_request": {}},
        "permissions": {"contents": "read"},
        "concurrency": {"group": "test", "cancel-in-progress": True},
        "jobs": {"test": job},
    }
    with pytest.raises(ValueError):
        CHECKS["workflow_policy"](workflow)
