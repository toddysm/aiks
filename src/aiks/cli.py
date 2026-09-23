"""Click command surface for AI on AKS workflows."""

from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import NoReturn

import click
from pydantic import ValidationError

from aiks import __version__
from aiks.config import EnvironmentConfig, load_environment_config, write_schema
from aiks.logging import configure_logging
from aiks.redaction import redact_text
from aiks.results import OperationResult

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


def _load(path: Path) -> EnvironmentConfig:
    try:
        return load_environment_config(path)
    except (ValueError, ValidationError) as error:
        LOGGER.error("configuration load failed: %s", redact_text(str(error)))
        raise click.ClickException(str(error)) from error


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


def _register_state_placeholder(name: str) -> None:
    @state.command(name)
    @CONFIG_OPTION
    def command(config_path: Path) -> None:
        _load(config_path)
        _pending(8)


_register_state_placeholder("bootstrap")
_register_state_placeholder("status")


@state.command("destroy")
@CONFIG_OPTION
@click.option("--allow-production-destroy", is_flag=True)
def state_destroy(config_path: Path, allow_production_destroy: bool) -> None:
    """Delete an unused Terraform backend with explicit safeguards."""

    config = _load(config_path)
    _confirm_destroy(config, allow_production_destroy)
    _pending(8)


@cli.group()
def local() -> None:
    """Manage the local kind validation cluster."""


def _register_local_placeholder(name: str) -> None:
    @local.command(name)
    @CONFIG_OPTION
    def command(config_path: Path) -> None:
        _load(config_path)
        _pending(9)


_register_local_placeholder("create")
_register_local_placeholder("delete")


@cli.group()
def workload() -> None:
    """Manage the readiness Helm release."""


def _register_workload_placeholder(name: str) -> None:
    @workload.command(name)
    @CONFIG_OPTION
    @TARGET_OPTION
    def command(config_path: Path, target: str) -> None:
        _load(config_path)
        del target
        _pending(9)


for _name in ("install", "verify", "upgrade", "rollback", "uninstall"):
    _register_workload_placeholder(_name)


if __name__ == "__main__":
    cli()
