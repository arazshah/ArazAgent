"""FastAPI app: lifespan, route mounting, webhook, health checks."""

from __future__ import annotations

import asyncio
import logging
import secrets as secrets_module
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response

from app.bootstrap import Bootstrap, load_bootstrap
from app.capture import CaptureContext, handle_update
from app.crypto import Crypto
from app.db import apply_schema, check_ready, create_pool
from app.providers.registry import ProviderRegistry
from app.settings_store import SettingsStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _polling_loop(app: FastAPI) -> None:
    """Long-polls getUpdates while bale.mode == 'polling'. Idles otherwise so
    toggling modes in the admin UI takes effect without a restart. Survives
    individual iteration failures with exponential backoff.
    """
    settings: SettingsStore = app.state.settings
    registry: ProviderRegistry = app.state.registry
    backoff = 1.0

    while True:
        try:
            if await settings.get_mode() != "polling":
                await asyncio.sleep(5)
                continue

            provider = await registry.get_bale_client()
            if provider is None:
                await asyncio.sleep(5)
                continue

            offset = await settings.get_poll_offset()
            updates = await provider.get_updates(offset=offset or None, timeout=25)

            for raw in updates:
                ctx = _build_capture_context(app, provider)
                try:
                    await handle_update(raw, ctx)
                except Exception:  # noqa: BLE001
                    logger.exception("polling: error handling update")
                update_id = raw.get("update_id")
                if isinstance(update_id, int):
                    await settings.set_poll_offset(update_id + 1)

            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("polling loop iteration failed")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _schedule(job) -> None:
    asyncio.create_task(job())


def _build_capture_context(app: FastAPI, provider) -> CaptureContext:
    return CaptureContext(
        pool=app.state.pool,
        settings=app.state.settings,
        provider=provider,
        schedule_background=_schedule,
        transcribe_voice=getattr(app.state, "transcribe_voice", None),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    boot: Bootstrap = app.state.bootstrap
    logging.getLogger().setLevel(boot.log_level)

    pool = await create_pool(boot.database_url)
    await apply_schema(pool)
    app.state.pool = pool

    crypto = Crypto(boot.secret_encryption_key)
    settings = SettingsStore(pool, crypto)
    registry = ProviderRegistry(settings)
    settings.set_reload_callback(registry.reload)
    app.state.crypto = crypto
    app.state.settings = settings
    app.state.registry = registry

    poll_task = asyncio.create_task(_polling_loop(app))

    logger.info("startup complete")
    try:
        yield
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        await registry.aclose()
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

    @app.post("/webhook/{secret}")
    async def webhook(secret: str, request: Request) -> dict:
        settings: SettingsStore = app.state.settings
        expected = await settings.get("bale.webhook_secret")
        # Compare against a constant on the "not configured" path too, so
        # timing never distinguishes "unconfigured" from "wrong secret".
        if not expected or not secrets_module.compare_digest(secret, expected):
            raise HTTPException(status_code=404)

        # Bale may or may not send this header; verify it only if present.
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header_secret is not None and not secrets_module.compare_digest(header_secret, expected):
            raise HTTPException(status_code=404)

        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            logger.error("webhook received non-JSON body")
            return {"ok": True}

        provider = await app.state.registry.get_bale_client()
        if provider is None:
            logger.error("no Bale client configured (bale.bot_token unset); dropping update")
            return {"ok": True}

        ctx = _build_capture_context(app, provider)
        try:
            await handle_update(raw, ctx)
        except Exception:  # noqa: BLE001
            # Always return 200 quickly, even on internal errors, so the
            # platform does not enter a redelivery storm.
            logger.exception("unhandled error processing webhook update")
        return {"ok": True}

    return app


app = create_app()
