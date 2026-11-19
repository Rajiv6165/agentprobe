"""FastAPI application initialization, middlewares, error handlers, and route registration."""

from __future__ import annotations

import logging
import sys
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import cassettes, health, ingest, projects, runs


class JsonFormatter(logging.Formatter):
    """Structured JSON logging formatter."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            log_obj["request_id"] = record.request_id
        if record.exc_info:
            log_obj["exc_info"] = self.formatException(record.exc_info)
        import json

        return json.dumps(log_obj)


# Initialize root logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=settings.LOG_LEVEL, handlers=[handler], force=True)
logger = logging.getLogger("agentprobe.api")

app = FastAPI(
    title="AgentProbe API",
    version="0.1.0",
    description="Deterministic ingestion and trace query API for AgentProbe testing framework.",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next: Any) -> Response:
    """Inject request ID, compute latency, enforce payload size limit, and log structured HTTP access details."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.MAX_CASSETTE_SIZE_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"Payload size {content_length} bytes exceeds limit of {settings.MAX_CASSETTE_SIZE_BYTES} bytes",
                    "details": None,
                }
            },
        )

    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start_time = time.perf_counter()

    # Pass request_id along in state
    request.state.request_id = request_id

    response: Response = await call_next(request)

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    response.headers["X-Request-ID"] = request_id

    # Avoid logging health checks at INFO level to keep logs clean
    if not request.url.path.startswith(("/health", "/ready")):
        logger.info(
            "%s %s %d - %sms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={"request_id": request_id},
        )

    return response


# Standardized error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        payload = {
            "error": {
                "code": exc.detail.get("code", "HTTP_ERROR"),
                "message": exc.detail.get("message", str(exc.detail)),
                "details": exc.detail.get("details"),
            }
        }
    else:
        payload = {
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
                "details": None,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    payload = {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Invalid request payload or parameters",
            "details": exc.errors(),
        }
    }
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=payload)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server exception: %s", exc)
    payload = {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An internal server error occurred",
            "details": str(exc) if settings.ENVIRONMENT == "development" else None,
        }
    }
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload)


# Register routers
app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(projects.router)
app.include_router(runs.router)
app.include_router(cassettes.router)
