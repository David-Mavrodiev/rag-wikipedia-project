import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.query import router as query_router
from app.core.config import settings
from app.db.session import dispose_engine, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield
    await dispose_engine()


app = FastAPI(title="RAG Wikipedia API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
    # The session cookie only rides along on cross-origin calls with this on.
    # Note that browsers reject credentialed requests when the allowed origin is
    # "*", so ALLOWED_ORIGINS must list real origins when the SPA is not served
    # from the same host as the API (the compose setup proxies it, so it is).
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# /health stays public: it is the container/ingress liveness probe, so it must
# answer before any credential is available.
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(query_router)
