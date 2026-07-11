"""
main.py — FastAPI application entry point.

Responsibilities:
  - Logging setup: configures console + rotating file logging before anything else.
  - Lifespan hook: loads the GGUF model into RAM once at startup.
  - CORS middleware: allows the Vite frontend on localhost:5173 to call the API.
  - Router mounting: registers all route groups under their correct prefixes.

Run with:
    uvicorn main:app --reload --port 8000
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from core.config import settings
from core.database import get_db  # Ensures models are registered
from llm.client import LLMClient
from routers.auth import router as auth_router
from routers.files import router as files_router
from routers.internal import router as internal_router
from routers.messages import router as messages_router
from routers.sessions import router as sessions_router
import hashlib
import logging

_logger = logging.getLogger("app.startup")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Startup: load the GGUF model into RAM.
    This runs once before the first request is accepted.
    The model (~2.5 GB) stays resident in memory for the entire process lifetime.
    """
    await LLMClient.initialize()

    # Log at startup (before yield) so the hash is visible when the server goes live.
    _logger.info(
        "Startup complete | system_prompt_md5=%s | debug=%s",
        hashlib.md5(settings.llm_system_prompt.encode()).hexdigest(),
        settings.debug,
    )

    yield

    # Teardown (if needed — e.g., explicit resource cleanup in the future)
    _logger.info("Shutdown initiated.")


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Personalized Chatbot Backend",
    description="Backend API for a locally-hosted Qwen3 chatbot.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Middleware ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    # allow_credentials=True is REQUIRED for httpOnly cookies to be sent cross-origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Monitoring ─────────────────────────────────────────────────────────────
# Add /internal/metrics Auto + Mejar all HTTP requests
Instrumentator().instrument(app).expose(app, endpoint="/internal/metrics")

# ── Routers ───────────────────────────────────────────────────────────────────

# Auth: register, login, refresh, logout
app.include_router(auth_router, prefix=f"{settings.api_prefix}/auth", tags=["auth"])

# Sessions: list, create, get (with history), rename, delete
app.include_router(sessions_router, prefix=f"{settings.api_prefix}/sessions", tags=["sessions"])

# Messages: send (SSE stream) + feedback submit/update
# Uses /api prefix directly because routes span /sessions/{id}/messages and /messages/{id}/feedback
app.include_router(messages_router, prefix=settings.api_prefix, tags=["messages"])

# Internal: health check + model status (no JWT required)
app.include_router(internal_router, prefix="/internal", tags=["internal"])

# Files: authenticated report download
app.include_router(files_router, prefix=f"{settings.api_prefix}/files", tags=["files"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
