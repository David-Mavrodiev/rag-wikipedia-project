import logging

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.query import router as query_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="RAG Wikipedia API", version="0.1.0")
app.include_router(health_router)
app.include_router(query_router)
