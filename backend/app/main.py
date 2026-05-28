"""Research backend runtime entry point.

This file stays intentionally small. It only bootstraps FastAPI, exposes
system-level health endpoints, and registers grouped domain routers.
All feature work should happen inside `app.modules`.
"""

import os as _os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.faros.api.faros_api import router as faros_router
from app.modules.code import router as code_router
from app.modules.idea import router as idea_router
from app.modules.paper import router as paper_router
from app.modules.platform import router as platform_router
from app.modules.review import router as review_router
from app.version import API_VERSION, APP_NAME, APP_VERSION, CAPABILITIES, RELEASE_PHASE, SERVICE_NAME

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize runtime prerequisites required by the release baseline."""
    try:
        from app.db.engine import init_db

        init_db()
        logger.info("Database initialized/verified on startup")
    except Exception:
        logger.exception("Database initialization failed during startup")
        raise
    yield


app = FastAPI(
    title=APP_NAME,
    description="Research-grade backend for scientific experiment execution and artifact management",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

_dev_origins = _os.environ.get("DEV_ALLOWED_ORIGINS", "").split(",") if _os.environ.get("DEV_ALLOWED_ORIGINS") else []
_default_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:5176",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5175",
    "http://127.0.0.1:5176",
    "http://localhost:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _dev_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/api/system/health")
async def health_check():
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "version": APP_VERSION,
    }


@app.get("/api/system/version")
async def version_info():
    llm_info = {}
    try:
        from app.llm.provider_client import get_provider_client

        client = get_provider_client()
        llm_info = client.get_capabilities()
    except Exception as exc:
        llm_info = {"error": str(exc), "configured": False}

    return {
        "api_version": API_VERSION,
        "backend_version": APP_VERSION,
        "phase": RELEASE_PHASE,
        "capabilities": CAPABILITIES,
        "llm": llm_info,
    }


@app.get("/api/system/config")
async def system_config():
    import os

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    db_path = os.path.join(data_dir, "app.db")

    db_exists = os.path.exists(db_path)
    db_size = os.path.getsize(db_path) if db_exists else 0

    migration_status = "unknown"
    migration_revision = None
    try:
        from app.db.engine import get_migration_status

        status = get_migration_status()
        migration_status = status.get("status", "unknown")
        migration_revision = status.get("revision")
    except ImportError:
        migration_status = "not_initialized"
    except Exception as exc:
        migration_status = f"error: {str(exc)}"

    return {
        "paths": {
            "base_dir": base_dir,
            "data_dir": data_dir,
            "db_path": db_path,
            "workspaces_dir": os.path.join(data_dir, "workspaces"),
            "artifacts_dir": os.path.join(data_dir, "artifacts"),
        },
        "db": {
            "exists": db_exists,
            "size_bytes": db_size,
            "migration_status": migration_status,
            "revision": migration_revision,
        },
        "env": {
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "not_set"),
            "LLM_MODEL": os.environ.get("LLM_MODEL", "not_set"),
            "BACKEND_PORT": os.environ.get("BACKEND_PORT", "8005"),
        },
    }


@app.get("/api/system/db-status")
async def db_status():
    import os

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(base_dir, "data", "app.db")

    result = {
        "ok": False,
        "path": db_path,
        "revision": None,
        "error": None,
    }

    try:
        from app.db.engine import get_migration_status, test_connection

        conn_ok = test_connection()
        if not conn_ok:
            result["error"] = "Connection test failed"
            return result

        status = get_migration_status()
        result["ok"] = status.get("status") == "current"
        result["revision"] = status.get("revision")

        if not result["ok"]:
            result["error"] = status.get("error", "Migration not current")
    except ImportError:
        result["error"] = "DB module not initialized - run migrations first"
    except Exception as exc:
        result["error"] = str(exc)

    return result


app.include_router(platform_router, prefix="/api/v1")
app.include_router(idea_router, prefix="/api/v1")
app.include_router(code_router, prefix="/api/v1")
app.include_router(paper_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")

app.include_router(faros_router, prefix="/api")
