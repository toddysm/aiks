"""Click command surface for AI on AKS workflows."""

from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Literal, NoReturn, cast

import click
from pydantic import ValidationError

from aiks import __version__
from aiks.config import EnvironmentConfig, load_environment_config, write_schema
from aiks.logging import configure_logging
from aiks.outputs import FoundationOutputs
from aiks.redaction import redact_text
from aiks.results import OperationResult
from aiks.state import StateBackend
from aiks.workload import NAMESPACE, WorkloadRuntime

LOGGER = logging.getLogger(__name__)
ENGINE = click.Choice(("bicep", "terraform"), case_sensitive=False)
TARGET = click.Choice(("kind", "aks"), case_sensitive=False)
CONFIG_OPTION = click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a nonsecret environment YAML file.",
)
ENGINE_OPTION = click.option("--engine", required=True, type=ENGINE)
TARGET_OPTION = click.option("--target", required=True, type=TARGET)


def _validation_message(error: ValidationError) -> str:
    lines = []
    for detail in error.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in detail["loc"])
        lines.append(f"{location}: {detail['msg']}")
    return "invalid configuration:\n" + "\n".join(lines)


def _load(path: Path) -> EnvironmentConfig:
    try:
        return load_environment_config(path)
    except ValidationError as error:
        message = redact_text(_validation_message(error))
        LOGGER.error("configuration load failed: %s", message)
        raise click.ClickException(message) from error
    except ValueError as error:
        message = redact_text(str(error))
        LOGGER.error("configuration load failed: %s", message)
        raise click.ClickException(message) from error


def _pending(issue: int) -> NoReturn:
    LOGGER.warning("command is pending implementation in GitHub issue #%s", issue)
    raise click.ClickException(f"command is not implemented yet; tracked by GitHub issue #{issue}")


def _confirm_destroy(config: EnvironmentConfig, allow_production_destroy: bool) -> None:
    environment = config.spec.environment
    if environment == "production" and not allow_production_destroy:
        raise click.UsageError("production destroy requires --allow-production-destroy")
    confirmation = click.prompt(f"Type {environment} to confirm destruction")
    if confirmation != environment:
        raise click.ClickException("destroy confirmation did not match the environment")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__)
def cli() -> None:
    """Build and validate reproducible AI workload foundations on AKS."""

    configure_logging()


@cli.group("config")
def config_group() -> None:
    """Manage the environment configuration contract."""


@config_group.command("schema")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def config_schema(output: Path) -> None:
    """Write the JSON Schema generated from the Pydantic model."""

    write_schema(output)
    click.echo(f"Wrote {output}")


@cli.group()
def infra() -> None:
    """Manage Azure infrastructure through Bicep or Terraform."""


@infra.command()
@CONFIG_OPTION
@ENGINE_OPTION
def preflight(config_path: Path, engine: str) -> None:
    """Check local and Azure prerequisites before planning."""

    _load(config_path)
    del engine
    _pending(11)


@infra.command()
@CONFIG_OPTION
@ENGINE_OPTION
@click.option("--json-output", type=click.Path(dir_okay=False, path_type=Path))
def validate(config_path: Path, engine: str, json_output: Path | None) -> None:
    """Validate the shared environment configuration."""

    started = perf_counter()
    config = _load(config_path)
    result = OperationResult(
        operation="infra.validate",
        phase="configuration",
        succeeded=True,
        duration_seconds=perf_counter() - started,
        context={
            "engine": engine,
            "environment": config.spec.environment,
            "location": config.spec.location,
        },
    )
    result.log()
    result.render()
    if json_output:
        result.write_json(json_output)


def _register_infra_placeholder(name: str, issue: int) -> None:
    @infra.command(name)
    @CONFIG_OPTION
    @ENGINE_OPTION
    def command(config_path: Path, engine: str) -> None:
        _load(config_path)
        del engine
        _pending(issue)


_register_infra_placeholder("plan", 11)
_register_infra_placeholder("deploy", 11)


@infra.command()
@CONFIG_OPTION
def verify(config_path: Path) -> None:
    """Verify deployed Azure and Kubernetes posture."""

    _load(config_path)
    _pending(11)


@infra.command()
@CONFIG_OPTION
@ENGINE_OPTION
@click.option("--allow-production-destroy", is_flag=True)
def destroy(config_path: Path, engine: str, allow_production_destroy: bool) -> None:
    """Destroy an environment with explicit safeguards."""

    config = _load(config_path)
    del engine
    _confirm_destroy(config, allow_production_destroy)
    _pending(11)


@cli.group()
def state() -> None:
    """Manage the Terraform Azure Storage backend."""


def _state_operation(
    config: EnvironmentConfig,
    operation: str,
    json_output: Path | None,
    *,
    allow_production: bool = False,
    delete_recovery: bool = False,
) -> None:
    started = perf_counter()
    try:
        service = StateBackend(config)
        if operation != "status":
            click.echo(
                f"Subscription: {service.subscription}; "
                f"state group: {config.spec.terraform.state_resource_group}"
            )
        if operation == "bootstrap":
            click.confirm("Create or update this Terraform backend?", abort=True)
            data = service.bootstrap()
        elif operation == "destroy":
            group = click.prompt("Type the state resource group to confirm backend deletion")
            if group != config.spec.terraform.state_resource_group:
                raise ValueError("backend group confirmation did not match")
            data = service.destroy(
                confirmed_environment=config.spec.environment,
                allow_production=allow_production,
                delete_recovery=delete_recovery,
            )
        else:
            data = service.status()
    except (ValueError, OSError) as error:
        message = redact_text(str(error))
        failure = OperationResult(
            operation=f"state.{operation}",
            phase="backend",
            succeeded=False,
            duration_seconds=perf_counter() - started,
            context={"environment": config.spec.environment, "error": message},
        )
        failure.log()
        if json_output:
            failure.write_json(json_output)
        raise click.ClickException(message) from error
    result = OperationResult(
        operation=f"state.{operation}",
        phase="backend",
        succeeded=True,
        duration_seconds=perf_counter() - started,
        context=data,
    )
    result.log()
    result.render()
    if json_output:
        result.write_json(json_output)


@state.command("bootstrap")
@CONFIG_OPTION
@click.option("--json-output", type=click.Path(dir_okay=False, path_type=Path))
def state_bootstrap(config_path: Path, json_output: Path | None) -> None:
    """Create the restricted backend and migrate its bootstrap state."""
    _state_operation(_load(config_path), "bootstrap", json_output)


@state.command("status")
@CONFIG_OPTION
@click.option("--json-output", type=click.Path(dir_okay=False, path_type=Path))
def state_status(config_path: Path, json_output: Path | None) -> None:
    """Inspect backend ownership, state keys, and leases without mutation."""
    _state_operation(_load(config_path), "status", json_output)


@state.command("destroy")
@CONFIG_OPTION
@click.option("--allow-production-destroy", is_flag=True)
@click.option("--delete-recovery-copy", is_flag=True)
@click.option("--json-output", type=click.Path(dir_okay=False, path_type=Path))
def state_destroy(
    config_path: Path,
    allow_production_destroy: bool,
    delete_recovery_copy: bool,
    json_output: Path | None,
) -> None:
    """Delete an unused Terraform backend with explicit safeguards."""

    config = _load(config_path)
    _confirm_destroy(config, allow_production_destroy)
    _state_operation(
        config,
        "destroy",
        json_output,
        allow_production=allow_production_destroy,
        delete_recovery=delete_recovery_copy,
    )


@cli.group()
def local() -> None:
    """Manage the local kind validation cluster."""


def _lifecycle_operation(
    config: EnvironmentConfig,
    operation: str,
    target: str,
    json_output: Path | None,
    *,
    kubeconfig: Path | None = None,
    context: str | None = None,
    outputs: Path | None = None,
    revision: int = 0,
    allow_production_change: bool = False,
) -> None:
    started = perf_counter()
    group = "local" if operation in {"create", "delete"} else "workload"
    try:
        foundation = FoundationOutputs.model_validate_json(outputs.read_text()) if outputs else None
        runtime = WorkloadRuntime(
            config,
            cast(Literal["kind", "aks"], target),
            kubeconfig=kubeconfig,
            context=context,
            outputs=foundation,
        )
        if target == "aks" and operation not in {"verify", "build"}:
            if config.spec.environment == "production" and not allow_production_change:
                raise ValueError("production workload changes require --allow-production-change")
            click.echo(
                f"AKS cluster: {foundation.cluster.name if foundation else ''}; context: {context}"
            )
            if (
                click.prompt("Type the environment to confirm the workload change")
                != config.spec.environment
            ):
                raise ValueError("environment confirmation did not match")
        if (
            operation == "delete"
            and click.prompt("Type the kind cluster name to confirm deletion")
            != config.spec.local.kind_cluster_name
        ):
            raise ValueError("cluster confirmation did not match")
        if (
            operation == "uninstall"
            and click.prompt("Type the workload namespace to confirm removal") != NAMESPACE
        ):
            raise ValueError("namespace confirmation did not match")
        if operation == "create":
            data = runtime.create_local()
        elif operation == "delete":
            data = runtime.delete_local()
        elif operation == "build":
            data = runtime.build()
        elif operation in {"install", "upgrade"}:
            data = runtime.install(upgrade=operation == "upgrade")
        elif operation == "rollback":
            data = runtime.rollback(revision)
        elif operation == "uninstall":
            data = runtime.uninstall()
        else:
            data = runtime.verify()
    except (ValueError, OSError) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        message = redact_text(message)
        result = OperationResult(
            operation=f"{group}.{operation}",
            phase="workload",
            succeeded=False,
            duration_seconds=perf_counter() - started,
            context={"target": target, "environment": config.spec.environment, "error": message},
        )
        result.log()
        if json_output:
            result.write_json(json_output)
        raise click.ClickException(message) from error
    result = OperationResult(
        operation=f"{group}.{operation}",
        phase="workload",
        succeeded=True,
        duration_seconds=perf_counter() - started,
        context=data,
    )
    result.log()
    result.render()
    if json_output:
        result.write_json(json_output)


def _register_local_command(name: str) -> None:
    @local.command(name)
    @CONFIG_OPTION
    @click.option("--json-output", type=click.Path(dir_okay=False, path_type=Path))
    def command(config_path: Path, json_output: Path | None) -> None:
        _lifecycle_operation(_load(config_path), name, "kind", json_output)


_register_local_command("create")
_register_local_command("delete")


@cli.group()
def workload() -> None:
    """Manage the readiness Helm release."""


def _register_workload_command(name: str) -> None:
    @workload.command(name)
    @CONFIG_OPTION
    @TARGET_OPTION
    @click.option("--kubeconfig", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--context")
    @click.option("--outputs", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--revision", type=click.IntRange(min=0), default=0)
    @click.option("--allow-production-change", is_flag=True)
    @click.option("--json-output", type=click.Path(dir_okay=False, path_type=Path))
    def command(
        config_path: Path,
        target: str,
        kubeconfig: Path | None,
        context: str | None,
        outputs: Path | None,
        revision: int,
        allow_production_change: bool,
        json_output: Path | None,
    ) -> None:
        _lifecycle_operation(
            _load(config_path),
            name,
            target,
            json_output,
            kubeconfig=kubeconfig,
            context=context,
            outputs=outputs,
            revision=revision,
            allow_production_change=allow_production_change,
        )


for _name in ("build", "install", "verify", "upgrade", "rollback", "uninstall"):
    _register_workload_command(_name)


if __name__ == "__main__":
    cli()
