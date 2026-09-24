"""Small configuration-driven workload for validating the foundation."""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter
from typing import Literal, Self
from urllib.parse import urlsplit

import yaml
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from pydantic import Field, ValidationError, model_validator

from aiks.config import StrictModel


class ReadinessSettings(StrictModel):
    environment: Literal["dev", "production"]
    target: Literal["kind", "aks"]
    cluster: str = Field(min_length=1, max_length=128)
    namespace: str = Field(min_length=1, max_length=63)
    workload: str = Field(min_length=1, max_length=63)
    ready: bool = True
    vault_uri: str = ""
    marker_key_name: str = "readiness-marker"

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.target == "aks":
            parsed = urlsplit(self.vault_uri)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not re.fullmatch(
                    r"[a-z][a-z0-9-]{1,22}[a-z0-9]\.vault\."
                    r"(?:azure\.net|usgovcloudapi\.net|azure\.cn)",
                    parsed.hostname,
                )
                or parsed.port is not None
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError(
                    "AKS identity verification requires a supported Azure Key Vault HTTPS URI"
                )
        if self.target == "kind" and self.vault_uri:
            raise ValueError("kind must not configure Azure identity verification")
        return self


def verify_identity(settings: ReadinessSettings) -> str:
    """Read public marker metadata using only the projected workload credential."""
    with (
        DefaultAzureCredential(
            exclude_environment_credential=True,
            exclude_managed_identity_credential=True,
            exclude_shared_token_cache_credential=True,
            exclude_visual_studio_code_credential=True,
            exclude_cli_credential=True,
            exclude_powershell_credential=True,
            exclude_developer_cli_credential=True,
            exclude_broker_credential=True,
            exclude_interactive_browser_credential=True,
        ) as credential,
        KeyClient(
            settings.vault_uri,
            credential,
            retry_total=0,
            connection_timeout=3,
            read_timeout=5,
        ) as client,
    ):
        key = client.get_key(settings.marker_key_name)
        if key.name != settings.marker_key_name:
            raise ValueError("unexpected marker key")
        return key.name


def create_app(
    settings: ReadinessSettings,
    identity_probe: Callable[[ReadinessSettings], str] = verify_identity,
) -> FastAPI:
    """Build isolated metrics and routes; no Azure request occurs at startup."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    registry = CollectorRegistry()
    dimensions = ("environment", "cluster", "namespace", "workload", "target")
    labels = (
        settings.environment,
        settings.cluster,
        settings.namespace,
        settings.workload,
        settings.target,
    )
    ready = Gauge(
        "aiks_readiness_info", "Application readiness", (*dimensions, "status"), registry=registry
    )
    ready.labels(*labels, "ready").set(int(settings.ready))
    failures = Counter(
        "aiks_readiness_failures", "Failed readiness probes", dimensions, registry=registry
    )
    duration = Histogram(
        "aiks_readiness_duration_seconds", "Readiness probe duration", dimensions, registry=registry
    )
    requests = Counter(
        "aiks_http_requests", "HTTP requests", (*dimensions, "path", "status"), registry=registry
    )
    latency = Histogram(
        "aiks_http_request_duration_seconds",
        "HTTP request duration",
        (*dimensions, "path"),
        registry=registry,
    )
    known_paths = {"/livez", "/readyz", "/identityz", "/metrics"}

    @app.middleware("http")
    async def record_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = perf_counter()
        response = await call_next(request)
        path = request.url.path if request.url.path in known_paths else "other"
        requests.labels(*labels, path, str(response.status_code)).inc()
        latency.labels(*labels, path).observe(perf_counter() - started)
        return response

    @app.get("/livez")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/readyz")
    def readiness() -> JSONResponse:
        started = perf_counter()
        if not settings.ready:
            failures.labels(*labels).inc()
        duration.labels(*labels).observe(perf_counter() - started)
        return JSONResponse(
            {"status": "ready" if settings.ready else "not-ready"},
            status_code=200 if settings.ready else 503,
        )

    @app.get("/identityz")
    def identity() -> JSONResponse:
        if settings.target == "kind":
            return JSONResponse({"status": "skipped", "reason": "local-target"})
        try:
            name = identity_probe(settings)
        except (AzureError, OSError, ValueError):
            return JSONResponse(
                {"status": "failed", "reason": "identity-verification-failed"}, status_code=503
            )
        return JSONResponse({"status": "verified", "keyName": name})

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

    return app


def app_factory() -> FastAPI:
    """Load the mounted nonsecret configuration before the server starts."""
    try:
        path = Path(os.environ["AIKS_READINESS_CONFIG"])
        settings = ReadinessSettings.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
    except (KeyError, OSError, yaml.YAMLError, ValidationError):
        raise ValueError("invalid or unavailable mounted readiness configuration") from None
    return create_app(settings)
