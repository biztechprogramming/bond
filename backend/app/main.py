"""FastAPI app entry point for the Bond backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.config import get_settings
from backend.app.db.session import get_db, get_session_factory, init_db
from backend.app.jobs import JobScheduler
from backend.app.jobs.sync_models_stdb import sync_models_stdb
from backend.app.jobs.recalculate_skill_scores import recalculate_skill_scores
from backend.app.jobs.sync_skills import sync_skills
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
from backend.app.api.v1.skills import router as skills_router
from backend.app.api.v1.optimization import router as optimization_router
from backend.app.api.v1.llm import router as llm_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    configure_logging()
    await init_db()

    # Background job scheduler - using SpacetimeDB
    scheduler = JobScheduler(get_session_factory())
    scheduler.register("sync_models", sync_models_stdb, interval_seconds=6 * 3600)
    scheduler.register("recalculate_skill_scores", recalculate_skill_scores, interval_seconds=6 * 3600)
    scheduler.register("sync_skills", sync_skills, interval_seconds=24 * 3600)
    await scheduler.start()
    app.state.scheduler = scheduler

    # MCP Setup (Design Doc 054: connection pools + health monitor)
    try:
        await mcp_manager.ensure_servers_loaded()
        mcp_manager.start_health_monitor()
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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch ALL unhandled exceptions and return them as structured JSON errors.
    
    No silent failures. Every error gets surfaced to the caller.
    """
    import logging
    import traceback
    logger = logging.getLogger("bond.api")
    logger.error("Unhandled exception on %s %s: %s\n%s", 
                 request.method, request.url.path, exc, traceback.format_exc())
    
    status_code = getattr(exc, "status_code", 500)
    detail = str(exc)
    
    # Don't leak internal details in production, but we're local-first
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "error_type": type(exc).__name__,
            "path": str(request.url.path),
        },
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
app.include_router(skills_router, prefix="/api/v1")
app.include_router(optimization_router, prefix="/api/v1")
app.include_router(llm_router, prefix="/api/v1")
