"""Cloud-free tests of live-test admission and workflow safety contracts."""

import importlib.util
import json
import runpy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "azure_acceptance", ROOT / "scripts/run_azure_acceptance.py"
)
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


@pytest.fixture
def environment():
    return {
        "AIKS_TEST_SHA": "a" * 40,
        "AIKS_APPROVED_SHA": "a" * 40,
        "AIKS_CAMPAIGN": "approved-test",
        "AIKS_MAX_HOURLY_USD": "5",
        "AIKS_RETAINED_COST_USD": "5",
        "AIKS_CONFIG_BUNDLE": json.dumps(
            [
                {"engine": engine, "config": {"spec": {"environment": posture}}}
                for engine, posture in controller.SCENARIOS
            ]
        ),
    }


def test_admission_preserves_budget_and_reserves_cleanup(environment):
    manifest = controller.admission(environment, 100)
    assert manifest["maxSpendUsd"] == "100"
    assert manifest["estimatedTotalUsd"] == "65"
    assert manifest["cleanupDeadline"] == 43300
    assert manifest["testDeadline"] == 39700
    assert len({config["spec"]["naming"]["prefix"] for config in manifest["configurations"]}) == 4
    assert controller.remaining(manifest, 39699) == 1
    with pytest.raises(ValueError, match="deadline"):
        controller.remaining(manifest, 39700)
    assert controller.remaining(manifest, 39700, cleanup=True) == 3600


@pytest.mark.parametrize(
    "field,value",
    [
        ("AIKS_TEST_SHA", "main"),
        ("AIKS_TEST_SHA", "b" * 40),
        ("AIKS_CAMPAIGN", "../escape"),
        ("AIKS_MAX_SPEND_USD", "101"),
        ("AIKS_MAX_SPEND_USD", "0"),
        ("AIKS_MAX_HOURS", "13"),
        ("AIKS_MAX_HOURS", "1"),
        ("AIKS_MAX_HOURLY_USD", "0"),
        ("AIKS_MAX_HOURLY_USD", "10"),
        ("AIKS_MAX_HOURLY_USD", "NaN"),
        ("AIKS_RETAINED_COST_USD", "Infinity"),
        ("AIKS_RETAINED_COST_USD", "-1"),
        ("AIKS_RETAINED_COST_USD", "invalid"),
        ("AIKS_CONFIG_BUNDLE", "null"),
        ("AIKS_CONFIG_BUNDLE", "[]"),
    ],
)
def test_admission_refuses_unsafe_settings(environment, field, value):
    environment[field] = value
    with pytest.raises(ValueError):
        controller.admission(environment, 100)


@pytest.mark.parametrize("bad_entry", [None, {}, {"engine": "unknown", "config": {}}])
def test_admission_refuses_malformed_scenario(environment, bad_entry):
    bundle = json.loads(environment["AIKS_CONFIG_BUNDLE"])
    bundle[0] = bad_entry
    environment["AIKS_CONFIG_BUNDLE"] = json.dumps(bundle)
    with pytest.raises(ValueError, match="scenario"):
        controller.admission(environment, 100)


def test_admission_refuses_reordered_postures(environment):
    bundle = json.loads(environment["AIKS_CONFIG_BUNDLE"])
    bundle[0], bundle[1] = bundle[1], bundle[0]
    environment["AIKS_CONFIG_BUNDLE"] = json.dumps(bundle)
    with pytest.raises(ValueError, match="order"):
        controller.admission(environment, 100)


@pytest.fixture
def campaign(environment, tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    environment.update(
        AIKS_RUN_ROOT=str(root), AIKS_PYTHON=sys.executable, RUNNER_TEMP=str(tmp_path)
    )
    return controller.Campaign(environment)


def test_campaign_reuse_cannot_reset_deadline(campaign, monkeypatch):
    campaign.load(create=True)
    deadline = campaign.manifest["testDeadline"]
    monkeypatch.setattr(controller.time, "time", lambda: deadline - 1)
    restarted = controller.Campaign(campaign.environment)
    restarted.load()
    assert restarted.manifest["testDeadline"] == deadline
    restarted.candidate["revision"] = "b" * 40
    with pytest.raises(ValueError, match="settings changed"):
        restarted.load()


def test_refuse_storage_symlink(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        controller.private_directory(link / "child")
    assert not (target / "child").exists()


def test_cli_passes_explicit_production_confirmation(campaign, monkeypatch):
    campaign.load(create=True)
    calls = []

    def execute(arguments, directory, timeout, stop_file, answer):
        calls.append((arguments, timeout, stop_file, answer))
        controller.save(
            Path(arguments[arguments.index("--json-output") + 1]),
            {
                "succeeded": True,
                "context": {"verified": True},
            },
        )

    monkeypatch.setattr(controller, "execute", execute)
    assert campaign.cli(1, "infra", "deploy") == {"verified": True}
    assert "--allow-production-deploy" in calls[0][0]
    assert calls[0][3] == "production\n"
    campaign.cli(1, "infra", "destroy", cleanup=True)
    assert "--allow-production-destroy" in calls[1][0]
    assert calls[1][2] is None


def test_failed_deploy_always_attempts_guarded_cleanup(campaign, monkeypatch):
    operations = []

    def cli(index, group, operation, **options):
        operations.append(operation)
        if operation == "deploy":
            raise RuntimeError("failure")
        return {}

    monkeypatch.setattr(campaign, "cli", cli)
    monkeypatch.setattr(campaign, "cleanup", lambda index: operations.append("cleanup"))
    with pytest.raises(RuntimeError, match="failure"):
        campaign.run(0)
    assert operations[-2:] == ["deploy", "cleanup"]
    assert campaign.stop_file.exists()
    with pytest.raises(ValueError, match="stopped"):
        campaign.run(0)


def test_successful_scenario_restores_config_and_cleans(campaign, monkeypatch):
    operations = []

    def cli(index, group, operation, **options):
        operations.append(operation)
        return {"noOp": True}

    def workload(index, operation):
        config = controller.read(campaign.directory / str(index) / "environment.json")
        if operation == "upgrade":
            assert config["spec"]["workload"]["replicas"] == 2
        else:
            assert "workload" not in config["spec"]
        operations.append(operation)

    monkeypatch.setattr(campaign, "cli", cli)
    monkeypatch.setattr(campaign, "workload", workload)
    monkeypatch.setattr(campaign, "cleanup", lambda index: operations.append("cleanup"))
    campaign.run(0)
    assert operations[-4:] == ["upgrade", "rollback", "verify", "cleanup"]
    assert campaign.manifest["scenarios"][0] == "passed"
    before = len(operations)
    campaign.run(0)
    assert len(operations) == before


def test_cleanup_uses_partial_guard_only_without_owner(campaign, monkeypatch):
    campaign.load(create=True)
    campaign.manifest["scenarios"][0] = "deploying"
    folder = campaign.directory / "0" / ".aiks" / "infra" / "owned"
    folder.mkdir(parents=True)
    controller.save(folder / "intent.json", {})
    calls = []
    monkeypatch.setattr(campaign, "cli", lambda *args, **kwargs: calls.append(kwargs))
    campaign.cleanup(0)
    assert calls == [{"cleanup": True, "extra": ("--allow-partial-cleanup",)}]
    assert campaign.manifest["scenarios"][0] == "cleaned"


def test_cleanup_failure_does_not_mark_passed(campaign, monkeypatch):
    monkeypatch.setattr(campaign, "cli", lambda *args, **kwargs: {"noOp": True})
    monkeypatch.setattr(campaign, "workload", lambda *args: None)

    def cleanup(index):
        raise ValueError("ownership refusal")

    monkeypatch.setattr(campaign, "cleanup", cleanup)
    with pytest.raises(ValueError, match="ownership"):
        campaign.run(0)
    assert campaign.manifest["scenarios"][0] != "passed"
    assert campaign.stop_file.exists()


def test_recovery_signals_running_process_without_stealing_lock(campaign):
    campaign.load(create=True)
    with (
        controller.campaign_lock(campaign.directory),
        pytest.raises(ValueError, match="active"),
    ):
        campaign.recover(expired_only=False)
    assert campaign.stop_file.exists()


def test_expiry_cleanup_leaves_active_campaign_alone(campaign):
    campaign.load(create=True)
    campaign.recover(expired_only=True)
    assert not campaign.stop_file.exists()


def test_execute_stops_and_never_echoes_private_output(tmp_path, capsys):
    controller.execute([sys.executable, "-c", "print('private-value')"], tmp_path, 5, None)
    assert "private-value" not in capsys.readouterr().out
    logs = list(tmp_path.glob("command-*.log"))
    assert len(logs) == 1 and logs[0].stat().st_mode & 0o077 == 0
    assert logs[0].read_text().strip() == "private-value"
    stop = tmp_path / "stop.json"
    stop.touch()
    with pytest.raises(TimeoutError):
        controller.execute([sys.executable, "-c", "input()"], tmp_path, 5, stop)
    assert len(list(tmp_path.glob("command-*.log"))) == 1


def test_summary_excludes_operator_configuration(campaign, capsys):
    campaign.load(create=True)
    campaign.summary()
    output = json.loads(capsys.readouterr().out)
    assert output["fullAcceptancePassed"] is False
    assert "configurations" not in output
    assert output["actualCostVerified"] is False


def test_workflows_keep_azure_out_of_pull_requests():
    directory = ROOT / ".github/workflows"
    main = yaml.load((directory / "azure-acceptance.yml").read_text(), Loader=yaml.BaseLoader)
    worker = yaml.load(
        (directory / "azure-acceptance-worker.yml").read_text(), Loader=yaml.BaseLoader
    )
    recovery = yaml.load(
        (directory / "azure-acceptance-cleanup.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(main["on"]) == {"workflow_dispatch"}
    assert set(worker["on"]) == {"workflow_call"}
    assert set(recovery["on"]) == {"workflow_dispatch", "workflow_run", "schedule"}
    job = worker["jobs"]["worker"]
    assert "refs/heads/main" in job["if"]
    assert "self-hosted" in job["runs-on"]
    assert job["timeout-minutes"] == "720"
    assert worker["permissions"] == {"contents": "read", "id-token": "write"}
    login = [step for step in job["steps"] if step.get("uses") == "azure/login@v2"]
    assert len(login) == 5
    assert all(set(step["with"]) == {"client-id", "tenant-id", "subscription-id"} for step in login)
    artifacts = [
        step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert len(artifacts) == 1
    assert artifacts[0]["with"]["path"] == "${{ runner.temp }}/acceptance-summary.json"
    assert main["concurrency"]["group"] != recovery["concurrency"]["group"]


def test_repository_policy_accepts_only_named_live_workflows():
    policy = runpy.run_path(str(ROOT / "scripts/check_repository.py"))["workflow_policy"]
    for name in (
        "azure-acceptance.yml",
        "azure-acceptance-cleanup.yml",
        "azure-acceptance-worker.yml",
    ):
        document = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        policy(document, workflow_name=name)
        with pytest.raises(ValueError):
            policy(document)
        document[True]["pull_request"] = {}
        with pytest.raises(ValueError, match="triggers"):
            policy(document, workflow_name=name)


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "unprotected"),
        ("runs-on", "ubuntu-latest"),
        ("timeout-minutes", 1000),
        ("if", "true"),
    ],
)
def test_repository_policy_rejects_unprotected_live_worker(field, value):
    policy = runpy.run_path(str(ROOT / "scripts/check_repository.py"))["workflow_policy"]
    document = yaml.safe_load((ROOT / ".github/workflows/azure-acceptance-worker.yml").read_text())
    document["jobs"]["worker"][field] = value
    with pytest.raises(ValueError, match="protected"):
        policy(document, workflow_name="azure-acceptance-worker.yml")
