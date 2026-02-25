"""FastAPI app entry point for the Bond backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.db.session import get_db, init_db
from backend.app.mediator import configure_logging
from backend.app.api.v1.health import router as health_router
from backend.app.api.v1.agent import router as agent_router
from backend.app.api.v1.agents import router as agents_router
from backend.app.api.v1.settings import router as settings_router
from backend.app.api.v1.conversations import router as conversations_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    configure_logging()
    await init_db()
    yield


app = FastAPI(
    title="Bond Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow the local frontend and gateway
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.frontend_port}",
        f"http://127.0.0.1:{settings.frontend_port}",
        f"http://localhost:{settings.gateway_port}",
        f"http://127.0.0.1:{settings.gateway_port}",
    ],
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
