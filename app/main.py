"""FastAPI app: lifespan, route mounting, webhook, health checks."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Response

from app.bootstrap import Bootstrap, load_bootstrap
from app.db import apply_schema, check_ready, create_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    boot: Bootstrap = app.state.bootstrap
    logging.getLogger().setLevel(boot.log_level)

    pool = await create_pool(boot.database_url)
    await apply_schema(pool)
    app.state.pool = pool

    logger.info("startup complete")
    try:
        yield
    finally:
        await pool.close()
        logger.info("shutdown complete")


def create_app(boot: Bootstrap | None = None) -> FastAPI:
    boot = boot or load_bootstrap()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.bootstrap = boot

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict:
        ok = await check_ready(app.state.pool)
        response.status_code = 200 if ok else 503
        return {"ok": ok}

    @app.get("/")
    async def root(response: Response) -> dict:
        response.status_code = 404
        return {}

    return app


app = create_app()
