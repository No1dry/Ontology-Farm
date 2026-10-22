"""Foundation API process and real-dependency health checks."""

from contextlib import asynccontextmanager
import logging
import re
import ssl
import tempfile
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from lzo.config import Settings
from lzo.storage.registry import StorageRegistry
from lzo.errors import ApiError

logger = logging.getLogger(__name__)


def _safe_details(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if any(secret in key.lower() for secret in
            ("password", "secret", "token", "credential", "database_url", "dsn",
             "authorization", "cookie", "api_key")) else _safe_details(item)
            for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_details(item) for item in value]
    if isinstance(value, str):
        # Details may contain unlabelled request input or exception context.
        return "[redacted]"
    return value


def _validation_details(error: RequestValidationError):
    details = []
    for issue in error.errors():
        location = issue.get("loc", ())
        source = location[0] if location else "request"
        if source in {"query", "path", "header", "cookie"} and len(location) > 1:
            field = location[1]
        else:
            field = source
        if not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field):
            field = "request"
        kind = issue.get("type", "invalid")
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z0-9_]+", kind):
            kind = "invalid"
        details.append({"field": field, "type": kind})
    return {"errors": details}


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    registry = StorageRegistry.from_settings(config)
    engine = registry.engine("core")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        registry.close()

    app = FastAPI(title="Liangzi Lake operational ontology API", version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    app.state.engine = engine
    app.state.storage_registry = registry
    from lzo.auth.router import router as auth_router
    from lzo.governance.history_router import router as history_router
    from lzo.artifacts.router import router as artifact_router
    from lzo.ingestion.router import router as ingestion_router
    from lzo.worker.router import router as worker_router
    from lzo.governance.approval_router import router as approval_router
    from lzo.ontology.router import router as ontology_router
    from lzo.semantic.router import router as semantic_router
    from lzo.maps.router import router as maps_router
    from lzo.catalog.router import router as catalog_router
    from lzo.telemetry.router import router as telemetry_router
    from lzo.auth import approval_policy  # register trusted F03 command policies
    from lzo.worker.runner import load_handlers
    load_handlers(config)
    app.include_router(auth_router)
    app.include_router(history_router)
    app.include_router(ingestion_router)
    app.include_router(artifact_router)
    app.include_router(worker_router)
    app.include_router(approval_router)
    app.include_router(ontology_router)
    app.include_router(semantic_router)
    app.include_router(maps_router)
    app.include_router(catalog_router)
    app.include_router(telemetry_router)

    # Multipart bytes are parsed under explicit bounds, so the typed metadata
    # command is registered in OpenAPI without eager framework body parsing.
    from fastapi.openapi.utils import get_openapi
    from lzo.ingestion.schemas import EvidenceCreate
    def ingestion_openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            command = EvidenceCreate.model_json_schema(ref_template="#/components/schemas/{model}")
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            for name, definition in command.pop("$defs", {}).items():
                components.setdefault(name, definition)
            components["EvidenceCreate"] = command
            app.openapi_schema = schema
        return app.openapi_schema
    app.openapi = ingestion_openapi

    def error_response(request: Request, code: str, status: int, message: str,
                       details=None, headers=None, trusted_details=False):
        request_id = getattr(request.state, "request_id", str(uuid4()))
        logger.warning("API error code=%s request_id=%s", code, request_id)
        return JSONResponse({"code": code, "message": message,
                             "request_id": request_id,
                             "details": details if trusted_details else _safe_details(details)},
                            status_code=status, headers=headers)

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied) else str(uuid4())
        response = await call_next(request)
        if request.url.path.startswith('/api/v1/maps/resources'):
            response.headers['Cache-Control'] = 'private,no-store'
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError):
        from lzo.semantic.semantic_profile import SemanticProfileError
        response = error_response(request, error.code, error.status, error.message, error.details,
                                  trusted_details=isinstance(error, SemanticProfileError))
        if error.code == 'INVALID_RANGE' and error.details:
            response.headers['Content-Range'] = f"bytes */{error.details['size_bytes']}"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return error_response(request, "VALIDATION_ERROR", 422, "Request validation failed",
                              _validation_details(error), trusted_details=True)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        codes = {401: ("UNAUTHENTICATED", "Authentication required"),
                 403: ("FORBIDDEN", "Operation forbidden"),
                 404: ("NOT_FOUND", "Resource not found"),
                 405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
                 409: ("CONFLICT", "Request conflict"),
                 422: ("VALIDATION_ERROR", "Request validation failed")}
        code, message = codes.get(error.status_code, ("HTTP_ERROR", "Request failed"))
        return error_response(request, code, error.status_code, message, headers=error.headers)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        dependencies = {name: "unavailable" for name in ("postgres", "fuseki", "oidc", "files")}
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            dependencies["postgres"] = "ready"
        except SQLAlchemyError:
            pass

        stores = registry.readiness() if config.storage_mode == "split" else {}
        for domain, status in stores.items():
            dependencies["store_" + domain] = status
        synchronization = {"pending": None, "failed": None}
        if config.storage_mode == "split" and stores.get("core") == "ready":
            try:
                with engine.connect() as connection:
                    synchronization = {"pending": connection.execute(text("SELECT count(*) FROM store_deliveries WHERE status IN ('pending','retry')")).scalar_one(),
                                       "failed": connection.execute(text("SELECT count(*) FROM store_deliveries WHERE status='failed'")).scalar_one()}
            except SQLAlchemyError:
                pass

        verify = ssl.create_default_context(cafile=str(config.ca_bundle)) if config.ca_bundle else True
        with httpx.Client(timeout=3, verify=verify) as client:
            try:
                response = client.get(config.fuseki_url.rstrip("/") + "/query", params={"query": "ASK {}"},
                                      headers={"Accept": "application/sparql-results+json"})
                response.raise_for_status()
                if isinstance(response.json().get("boolean"), bool):
                    dependencies["fuseki"] = "ready"
            except (httpx.HTTPError, ValueError):
                pass
            try:
                response = client.get(config.oidc_discovery_url)
                response.raise_for_status()
                if response.json().get("issuer") == config.oidc_issuer:
                    dependencies["oidc"] = "ready"
            except (httpx.HTTPError, ValueError):
                pass

        try:
            config.file_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=config.file_root) as probe:
                probe.write(b"ready")
                probe.flush()
            if config.blob_provider == "s3":
                from lzo.storage.blobs import get_blob_store
                get_blob_store(config).ready()
            dependencies["files"] = "ready"
        except (OSError, ApiError):
            pass

        available = all(status == "ready" for status in dependencies.values())
        return JSONResponse({"status": "ready" if available else "unavailable", "dependencies": dependencies, "synchronization": synchronization},
                            status_code=200 if available else 503)

    return app
