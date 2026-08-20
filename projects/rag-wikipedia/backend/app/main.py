import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.quality import router as quality_router
from app.api.query import router as query_router
from app.core.config import settings
from app.core.rate_limit import RateLimitMiddleware
from app.core.runtime_config import (
    CONFIG_PATH,
    apply_runtime_config,
    get_runtime_config,
    load_runtime_config,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

logger = logging.getLogger(__name__)


def restore_runtime_config(path: Path | None = None) -> None:
    """Re-apply a persisted auto-correction, and say so out loud.

    A successful `--auto-correct` audit rewrites the retrieval thresholds and
    saves them. Without this, that file was never read back and the correction
    silently evaporated on restart.

    It is logged at INFO on every boot because these five values decide what the
    API refuses. A config that is merely WRONG rather than malformed - say a
    refusal_min_score of 0.99, which refuses everything - is still valid, so the
    startup log is the only place a human sees that the running thresholds are
    not the defaults.
    """
    # Resolved once and used for BOTH the existence check and the load. Checking
    # one path and loading another is how the two silently drift apart.
    config_path = path or CONFIG_PATH

    if not config_path.exists():
        logger.info("No persisted retrieval config; using defaults %s", get_runtime_config())
        return

    try:
        config = load_runtime_config(config_path)
    except (OSError, ValueError) as exc:
        logger.warning(
            "Ignoring unusable %s (%s); using defaults %s", config_path, exc, get_runtime_config()
        )
        return

    apply_runtime_config(config)
    logger.info("Restored retrieval config from %s: %s", config_path, config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    restore_runtime_config()
    yield


app = FastAPI(title="RAG Wikipedia API", version="0.1.0", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(quality_router)
app.include_router(query_router)
