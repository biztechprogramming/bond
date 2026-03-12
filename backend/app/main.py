"""FastAPI app entry point for the Bond backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.db.session import get_db, get_session_factory, init_db
from backend.app.jobs import JobScheduler
from backend.app.jobs.sync_models_stdb import sync_models_stdb
from backend.app.mcp import mcp_manager, MCPServerConfig
from backend.app.mediator import configure_logging
from backend.app.api.v1.health import router as health_router
from backend.app.api.v1.agent import router as agent_router
from backend.app.api.v1.agents import router as agents_router
from backend.app.api.v1.settings import router as settings_router
from backend.app.api.v1.conversations import router as conversations_router
from backend.app.api.v1.memory import router as memory_router
from backend.app.api.v1.prompts import router as prompts_router
from backend.app.api.v1.plans import router as plans_router, items_router
from backend.app.api.v1.mcp import router as mcp_router
from backend.app.api.v1.deployments import router as deployments_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    configure_logging()
    await init_db()

    # Background job scheduler - using SpacetimeDB
    scheduler = JobScheduler(get_session_factory())
    scheduler.register("sync_models", sync_models_stdb, interval_seconds=6 * 3600)
    await scheduler.start()
    app.state.scheduler = scheduler

    # MCP Setup (Load enabled servers from SpacetimeDB)
    try:
        # NO SQLITE FALLBACK - use SpacetimeDB directly
        await mcp_manager.load_servers_from_db(None)
    except Exception as e:
        import logging
        logging.getLogger("bond.mcp").error(f"Failed to load MCP servers on startup: {e}")

    yield

    await mcp_manager.stop_all()
    await scheduler.stop()


app = FastAPI(
    title="Bond Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow the local frontend and gateway
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def inject_db_session(request: Request, call_next):
    """Inject a database session into request.state for the mediator."""
    async for session in get_db():
        request.state.db = session
        response = await call_next(request)
        return response


# Routes
app.include_router(health_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(agents_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(memory_router, prefix="/api/v1")
app.include_router(prompts_router, prefix="/api/v1")
app.include_router(plans_router, prefix="/api/v1")
app.include_router(items_router, prefix="/api/v1")
app.include_router(mcp_router, prefix="/api/v1")
app.include_router(deployments_router, prefix="/api/v1")
