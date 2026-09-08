"""Admin UI: login, settings page, test-connection buttons, webhook panel,
status panel. Mounted at ADMIN_PATH. No admin UI route is reachable without
a password configured, not even the login page — a missing password hash
(both DB and env) returns 503 site-wide under this router.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import items_view, tz
from app.admin.forms import GROUPS
from app.bootstrap import Bootstrap
from app.db import check_ready
from app.decisions_log import count_overrides, recent_overrides
from app.jalali import format_deadline
from app.labels import DECISION_LABELS, TYPE_LABELS
from app.llm import test_chat_connection
from app.security import (
    LOGIN_CONSTANT_DELAY_SECONDS,
    SESSION_COOKIE_NAME,
    LoginRateLimiter,
    client_ip,
    create_session_cookie,
    csrf_token_for,
    verify_csrf,
    verify_password,
    verify_session_cookie,
)
from app.settings_store import SettingsError, SettingsStore

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_GROUP_LABELS = {
    "bale": "بله",
    "llm": "هوش مصنوعی",
    "transcription": "رونویسی",
    "system": "سیستم",
    "review": "مرور دوره‌ای",
    "reminder": "یادآوری",
    "constitution": "قانون اساسی",
    "dedup": "موارد تکراری",
    "morning_brief": "خلاصه صبحگاهی",
}

_GROUP_ICONS = {
    "bale": "🤖",
    "llm": "🧠",
    "transcription": "🎙️",
    "system": "⚙️",
    "review": "🗓️",
    "reminder": "⏰",
    "constitution": "📜",
    "dedup": "🧬",
    "morning_brief": "☀️",
}

# Extra sidebar tabs that aren't settings-form groups (webhook/admin panels).
_EXTRA_TAB_LABELS = {"webhook": "وبهوک", "admin": "مدیر"}
_EXTRA_TAB_ICONS = {"webhook": "🔗", "admin": "🔐"}
_TAB_LABELS = {**_GROUP_LABELS, **_EXTRA_TAB_LABELS}
_TAB_ICONS = {**_GROUP_ICONS, **_EXTRA_TAB_ICONS}

# Sidebar grouping: (section label, tab ids). Order here is the render order.
_NAV_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("هسته", ("bale", "llm", "transcription")),
    ("رفتار و تصمیم‌گیری", ("constitution", "reminder", "review", "dedup", "morning_brief")),
    ("سیستم", ("system", "webhook", "admin")),
)

_ALL_TAB_IDS = frozenset(_TAB_LABELS)
_DEFAULT_TAB = "bale"


def _resolve_tab(raw: str | None) -> str:
    return raw if raw in _ALL_TAB_IDS else _DEFAULT_TAB


async def _ordered_counts(counts_coro, key_order: tuple[str, ...]) -> dict[str, int]:
    """Reshapes a {key: count} dict (arbitrary GROUP BY order) into a fixed
    key order with 0 for any missing key, so the overview tiles on
    items.html render in the same position every time instead of jumping
    around based on whatever order Postgres happened to return.
    """
    counts = await counts_coro
    return {key: counts.get(key, 0) for key in key_order}


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["jalali"] = format_deadline
    return templates


async def _current_epoch(settings: SettingsStore) -> int:
    raw = await settings.get("admin.session_epoch") or "1"
    try:
        return int(raw)
    except ValueError:
        return 1


async def _password_configured(settings: SettingsStore) -> bool:
    return bool(await settings.get("admin.password_hash"))


def _get_session_cookie(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


async def _require_session(request: Request, boot: Bootstrap, settings: SettingsStore) -> bool:
    token = _get_session_cookie(request)
    if not token:
        return False
    epoch = await _current_epoch(settings)
    return verify_session_cookie(boot.session_secret, token, epoch)


def _csrf_for(boot: Bootstrap, session_token: str | None) -> str:
    return csrf_token_for(boot.session_secret, session_token or "")


def build_admin_router(boot: Bootstrap) -> APIRouter:
    router = APIRouter()
    templates = _templates()
    rate_limiter = LoginRateLimiter()

    def _settings(request: Request) -> SettingsStore:
        return request.app.state.settings  # type: ignore[no-any-return]

    async def _service_unavailable_if_no_password(request: Request) -> HTMLResponse | None:
        settings = _settings(request)
        if not await _password_configured(settings):
            return HTMLResponse(
                "پیکربندی نشده: رمز عبور مدیر تنظیم نشده است. "
                "اسکریپت scripts/set_admin_password.py را اجرا کنید.",
                status_code=503,
            )
        return None

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        unavailable = await _service_unavailable_if_no_password(request)
        if unavailable is not None:
            return unavailable
        return templates.TemplateResponse(
            request, "login.html", {"admin_path": boot.admin_path, "error": None}
        )

    @router.post("/login")
    async def login_submit(request: Request):
        unavailable = await _service_unavailable_if_no_password(request)
        if unavailable is not None:
            return unavailable

        settings = _settings(request)
        ip = client_ip(request, boot.trust_proxy_headers)
        form = await request.form()
        password = str(form.get("password", ""))

        start = time.monotonic()
        try:
            if rate_limiter.is_locked_out(ip):
                logger.warning("admin login rate-limited ip=%s", ip)
                return PlainTextResponse("too many attempts", status_code=429)

            password_hash = await settings.get("admin.password_hash")
            ok = password_hash is not None and verify_password(password_hash, password)

            if not ok:
                rate_limiter.record_failure(ip)
                logger.warning("admin login failed ip=%s", ip)
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    {"admin_path": boot.admin_path, "error": "رمز عبور نادرست است"},
                    status_code=401,
                )

            rate_limiter.record_success(ip)
            logger.warning("admin login succeeded ip=%s", ip)
            epoch = await _current_epoch(settings)
            token = create_session_cookie(boot.session_secret, epoch)
            resp = RedirectResponse(f"{boot.admin_path}/settings", status_code=302)
            resp.set_cookie(
                SESSION_COOKIE_NAME,
                token,
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=12 * 3600,
            )
            return resp
        finally:
            elapsed = time.monotonic() - start
            if elapsed < LOGIN_CONSTANT_DELAY_SECONDS:
                await asyncio.sleep(LOGIN_CONSTANT_DELAY_SECONDS - elapsed)

    @router.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        resp = RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        resp.delete_cookie(SESSION_COOKIE_NAME)
        return resp

    async def _build_group_context(settings: SettingsStore, group_name: str, errors: dict) -> dict:
        fields = []
        for spec in GROUPS[group_name]:
            resolved = await settings.resolve(spec.key)
            fields.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "is_secret": spec.is_secret,
                    "value": None if spec.is_secret else resolved.value,
                    "masked_hint": resolved.masked_hint,
                    "source": resolved.source,
                    "field_error": errors.get(spec.key),
                }
            )
        return {"label": _GROUP_LABELS[group_name], "fields": fields, "error": None}

    async def _admin_info_context(settings: SettingsStore) -> dict:
        resolved = await settings.resolve("admin.password_hash")
        return {"source": resolved.source}

    async def _status_context(request: Request) -> dict:
        pool = request.app.state.pool
        settings = _settings(request)
        return {
            "total": await tz.count_total(pool),
            "today": await tz.count_captured_today(pool),
            "db_ok": await check_ready(pool),
            "git_sha": boot.git_sha,
            "mode": await settings.get_mode(),
            "by_transcript_status": await tz.count_by_transcript_status(pool),
            "pending_triage": await tz.count_pending_triage(pool),
            "missing_embeddings": await tz.count_missing_embeddings(pool),
        }

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        unavailable = await _service_unavailable_if_no_password(request)
        if unavailable is not None:
            return unavailable
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)

        settings = _settings(request)
        groups = {name: await _build_group_context(settings, name, {}) for name in GROUPS}
        webhook_url = await settings.get("bale.public_base_url")
        webhook_secret = await settings.get("bale.webhook_secret")
        webhook_display = f"{webhook_url}/webhook/***" if webhook_url and webhook_secret else None

        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "admin_path": boot.admin_path,
                "active_page": "settings",
                "group_icons": _GROUP_ICONS,
                "groups": groups,
                "nav_sections": _NAV_SECTIONS,
                "tab_labels": _TAB_LABELS,
                "tab_icons": _TAB_ICONS,
                "active_tab": _resolve_tab(request.query_params.get("tab")),
                "csrf_token": _csrf_for(boot, _get_session_cookie(request)),
                "flash": request.query_params.get("flash"),
                "webhook": {"url": webhook_display},
                "status": await _status_context(request),
                "test_results": {},
                "admin_info": await _admin_info_context(settings),
            },
        )

    @router.post("/settings/{group_name}")
    async def settings_submit(request: Request, group_name: str):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        if group_name not in GROUPS:
            return PlainTextResponse("unknown group", status_code=404)

        settings = _settings(request)
        form = await request.form()
        if not verify_csrf(
            boot.session_secret,
            _get_session_cookie(request) or "",
            str(form.get("csrf_token", "")),
        ):
            logger.warning(
                "settings submit rejected: bad csrf token group=%s ip=%s",
                group_name,
                client_ip(request, boot.trust_proxy_headers),
            )
            return PlainTextResponse("bad csrf token", status_code=400)

        ip = client_ip(request, boot.trust_proxy_headers)
        errors: dict[str, str] = {}
        for spec in GROUPS[group_name]:
            raw_value = form.get(spec.key)
            if raw_value is None:
                continue
            value = str(raw_value)
            if spec.is_secret and value == "":
                continue  # empty secret field leaves the stored value unchanged
            try:
                cleaned = spec.validate(value)
            except ValueError as exc:
                errors[spec.key] = str(exc)
                continue
            if not spec.is_secret and cleaned == "":
                continue
            await settings.set(spec.key, cleaned, actor_ip=ip)

        if errors:
            logger.warning(
                "settings submit rejected: field validation errors group=%s errors=%s ip=%s",
                group_name,
                errors,
                ip,
            )
            groups = {name: await _build_group_context(settings, name, {}) for name in GROUPS}
            groups[group_name] = await _build_group_context(settings, group_name, errors)
            return templates.TemplateResponse(
                request,
                "settings.html",
                {
                    "admin_path": boot.admin_path,
                    "active_page": "settings",
                    "group_icons": _GROUP_ICONS,
                    "groups": groups,
                    "nav_sections": _NAV_SECTIONS,
                    "tab_labels": _TAB_LABELS,
                    "tab_icons": _TAB_ICONS,
                    "active_tab": _resolve_tab(group_name),
                    "csrf_token": _csrf_for(boot, _get_session_cookie(request)),
                    "flash": None,
                    "webhook": {"url": None},
                    "status": await _status_context(request),
                    "test_results": {},
                    "admin_info": await _admin_info_context(settings),
                },
                status_code=400,
            )

        return RedirectResponse(
            f"{boot.admin_path}/settings?flash=ذخیره+شد&tab={group_name}", status_code=302
        )

    @router.post("/settings/clear/{key}")
    async def settings_clear(request: Request, key: str):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)

        settings = _settings(request)
        form = await request.form()
        if not verify_csrf(
            boot.session_secret,
            _get_session_cookie(request) or "",
            str(form.get("csrf_token", "")),
        ):
            return PlainTextResponse("bad csrf token", status_code=400)

        try:
            await settings.clear(key, actor_ip=client_ip(request, boot.trust_proxy_headers))
        except SettingsError:
            return PlainTextResponse("unknown key", status_code=404)
        tab = _resolve_tab(key.split(".", 1)[0])
        return RedirectResponse(
            f"{boot.admin_path}/settings?flash=پاک+شد&tab={tab}", status_code=302
        )

    @router.post("/settings/invalidate-sessions")
    async def invalidate_sessions(request: Request):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        settings = _settings(request)
        form = await request.form()
        if not verify_csrf(
            boot.session_secret,
            _get_session_cookie(request) or "",
            str(form.get("csrf_token", "")),
        ):
            return PlainTextResponse("bad csrf token", status_code=400)

        epoch = await _current_epoch(settings)
        await settings.set(
            "admin.session_epoch",
            str(epoch + 1),
            actor_ip=client_ip(request, boot.trust_proxy_headers),
        )
        resp = RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        resp.delete_cookie(SESSION_COOKIE_NAME)
        return resp

    @router.post("/settings/test/{group_name}")
    async def settings_test(request: Request, group_name: str):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        if group_name not in GROUPS:
            return PlainTextResponse("unknown group", status_code=404)

        settings = _settings(request)
        form = await request.form()
        if not verify_csrf(
            boot.session_secret,
            _get_session_cookie(request) or "",
            str(form.get("csrf_token", "")),
        ):
            return PlainTextResponse("bad csrf token", status_code=400)

        result_text = await _run_connection_test(request, group_name)

        groups = {name: await _build_group_context(settings, name, {}) for name in GROUPS}
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "admin_path": boot.admin_path,
                "active_page": "settings",
                "group_icons": _GROUP_ICONS,
                "groups": groups,
                "nav_sections": _NAV_SECTIONS,
                "tab_labels": _TAB_LABELS,
                "tab_icons": _TAB_ICONS,
                "active_tab": _resolve_tab(group_name),
                "csrf_token": _csrf_for(boot, _get_session_cookie(request)),
                "flash": None,
                "webhook": {"url": None},
                "status": await _status_context(request),
                "test_results": {group_name: result_text},
                "admin_info": await _admin_info_context(settings),
            },
        )

    async def _run_connection_test(request: Request, group_name: str) -> str:
        settings = _settings(request)
        if group_name == "bale":
            provider = await request.app.state.registry.get_bale_client()
            if provider is None:
                return "خطا: bale.bot_token تنظیم نشده است"
            try:
                me = await provider.get_me()
                info = await provider.get_webhook_info()
                username = (me.get("result") or {}).get("username", "?")
                return f"getMe: {username}\ngetWebhookInfo: {info}"
            except Exception as exc:  # noqa: BLE001 - shown to the owner, never a credential
                return f"خطا: {exc}"

        if group_name == "llm":
            base_url = await settings.get("llm.base_url")
            api_key = await settings.get("llm.api_key")
            model = await settings.get("llm.chat_model")
            if not (base_url and api_key and model):
                return "خطا: تنظیمات LLM کامل نیست"
            result = await test_chat_connection(base_url, api_key, model)
            if result.get("ok"):
                return f"OK — model={result['model']} latency={result['latency_ms']}ms"
            return f"خطا: {result.get('error')}"

        if group_name == "transcription":
            backend = await settings.get("transcription.backend")
            if backend == "local":
                try:
                    import faster_whisper  # noqa: F401
                except ImportError:
                    return "خطا: افزونه local نصب نشده است (pip install .[local])"
                return "OK — افزونه local موجود است"
            base_url = await settings.get("llm.base_url")
            api_key = await settings.get("llm.api_key")
            if not (base_url and api_key):
                return "خطا: تنظیمات AvalAI کامل نیست"
            return "OK — از تنظیمات LLM مشترک استفاده می‌شود"

        return "نامشخص"

    @router.post("/webhook/register")
    async def webhook_register(request: Request):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        settings = _settings(request)
        base_url = await settings.get("bale.public_base_url")
        secret = await settings.get("bale.webhook_secret")
        provider = await request.app.state.registry.get_bale_client()
        if provider is None or not base_url or not secret:
            return RedirectResponse(
                f"{boot.admin_path}/settings?flash=تنظیمات+بله+کامل+نیست&tab=webhook",
                status_code=302,
            )
        await provider.set_webhook(f"{base_url}/webhook/{secret}", secret)
        return RedirectResponse(
            f"{boot.admin_path}/settings?flash=وبهوک+ثبت+شد&tab=webhook", status_code=302
        )

    @router.post("/webhook/delete")
    async def webhook_delete(request: Request):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        provider = await request.app.state.registry.get_bale_client()
        if provider is not None:
            await provider.delete_webhook()
        return RedirectResponse(
            f"{boot.admin_path}/settings?flash=وبهوک+حذف+شد&tab=webhook", status_code=302
        )

    @router.post("/recover")
    async def recover(request: Request):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        from app.recovery import (
            recover_missing_embeddings,
            recover_stuck_transcriptions,
            recover_stuck_triage,
        )

        transcribed = await recover_stuck_transcriptions(request.app)
        triaged = await recover_stuck_triage(request.app)
        embedded = await recover_missing_embeddings(request.app)
        return RedirectResponse(
            f"{boot.admin_path}/settings?flash="
            f"بازیابی+{transcribed}+رونویسی+و+{triaged}+دسته‌بندی+و+{embedded}+embedding",
            status_code=302,
        )

    @router.get("/items", response_class=HTMLResponse)
    async def items_page(request: Request):
        unavailable = await _service_unavailable_if_no_password(request)
        if unavailable is not None:
            return unavailable
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)

        pool = request.app.state.pool
        item_type = request.query_params.get("type") or ""
        status = request.query_params.get("status") or ""
        only_commitments = request.query_params.get("commitments") == "1"
        if item_type not in items_view.VALID_TYPES:
            item_type = ""
        if status not in items_view.VALID_STATUSES:
            status = ""
        try:
            page = max(int(request.query_params.get("page", "1")), 1)
        except ValueError:
            page = 1
        offset = (page - 1) * items_view.PAGE_SIZE

        rows = await items_view.list_items(
            pool,
            item_type or None,
            status or None,
            offset=offset,
            only_commitments=only_commitments,
        )
        total = await items_view.count_items(
            pool, item_type or None, status or None, only_commitments=only_commitments
        )
        total_pages = max((total + items_view.PAGE_SIZE - 1) // items_view.PAGE_SIZE, 1)
        daily = await items_view.captured_per_day(pool)
        max_daily = max((count for _, count in daily), default=0)

        return templates.TemplateResponse(
            request,
            "items.html",
            {
                "admin_path": boot.admin_path,
                "active_page": "items",
                "csrf_token": _csrf_for(boot, _get_session_cookie(request)),
                "flash": request.query_params.get("flash"),
                "items": rows,
                "type_labels": TYPE_LABELS,
                "decision_labels": DECISION_LABELS,
                "filter_type": item_type,
                "filter_status": status,
                "filter_commitments": only_commitments,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "by_type": await _ordered_counts(
                    items_view.count_by_type(pool), items_view.VALID_TYPES
                ),
                "by_status": await _ordered_counts(
                    items_view.count_by_status(pool), items_view.VALID_STATUSES
                ),
                "daily": daily,
                "max_daily": max_daily,
                "override_count": await count_overrides(pool),
                "recent_overrides": await recent_overrides(pool),
            },
        )

    def _items_redirect_qs(form) -> str:
        # Rebuilt from individually validated parts rather than trusting a
        # raw querystring round-tripped through a hidden field — form data
        # is client-supplied, and this ends up in a redirect Location.
        item_type = str(form.get("redirect_type", ""))
        status = str(form.get("redirect_status", ""))
        if item_type not in items_view.VALID_TYPES:
            item_type = ""
        if status not in items_view.VALID_STATUSES:
            status = ""
        try:
            page = max(int(str(form.get("redirect_page", "1"))), 1)
        except ValueError:
            page = 1
        commitments = "1" if str(form.get("redirect_commitments", "")) == "1" else ""
        return f"?type={item_type}&status={status}&page={page}&commitments={commitments}"

    @router.post("/items/{item_id}/toggle")
    async def item_toggle(request: Request, item_id: int):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        form = await request.form()
        if not verify_csrf(
            boot.session_secret,
            _get_session_cookie(request) or "",
            str(form.get("csrf_token", "")),
        ):
            return PlainTextResponse("bad csrf token", status_code=400)

        new_status = str(form.get("new_status", ""))
        if new_status not in items_view.VALID_STATUSES:
            return PlainTextResponse("invalid status", status_code=400)

        await items_view.set_item_status(request.app.state.pool, item_id, new_status)
        return RedirectResponse(
            f"{boot.admin_path}/items{_items_redirect_qs(form)}", status_code=302
        )

    @router.post("/items/{item_id}/edit")
    async def item_edit(request: Request, item_id: int):
        if not await _require_session(request, boot, _settings(request)):
            return RedirectResponse(f"{boot.admin_path}/login", status_code=302)
        form = await request.form()
        if not verify_csrf(
            boot.session_secret,
            _get_session_cookie(request) or "",
            str(form.get("csrf_token", "")),
        ):
            return PlainTextResponse("bad csrf token", status_code=400)

        title = str(form.get("title", "")).strip()
        if not title:
            return PlainTextResponse("title required", status_code=400)

        await items_view.set_item_title(request.app.state.pool, item_id, title)
        return RedirectResponse(
            f"{boot.admin_path}/items{_items_redirect_qs(form)}", status_code=302
        )

    return router


def mount_admin(app: FastAPI, boot: Bootstrap) -> None:
    app.include_router(build_admin_router(boot), prefix=boot.admin_path)
    app.mount(
        f"{boot.admin_path}/static", StaticFiles(directory=str(STATIC_DIR)), name="admin-static"
    )

    @app.middleware("http")
    async def admin_security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(boot.admin_path):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = "default-src 'self'"
            # CSRF tokens are bound to the session cookie value at render time.
            # A cached copy of this page (browser disk cache or back-forward
            # cache) can outlive a logout/login cycle and submit a token tied
            # to a stale session, which the server correctly rejects with 400.
            # Never let these pages be cached so the form is always fresh.
            if not request.url.path.startswith(f"{boot.admin_path}/static"):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
        return response
