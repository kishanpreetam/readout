"""Readout Globe API server, per globe/API.md.

One read-only DuckDB connection opened through readout.sqlguard.sandbox_connection (external access off,
configuration locked), with a cursor per request. Request values are validated in globe.queries and bound as
SQL parameters. The outermost layer (Guard) checks the Host header exactly, refuses cross-origin writes,
adds the security headers to every response, static files and errors included, and turns anything
unexpected into a JSON 500 that carries the same headers.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import mimetypes
import threading
from collections.abc import Iterable, Iterator
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal
from pathlib import Path

import duckdb
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from readout import config, sqlguard

from . import ask as asker
from . import queries

log = logging.getLogger("globe")

HOST, PORT = "127.0.0.1", 8091
ALLOWED_HOSTS = (f"{HOST}:{PORT}", f"localhost:{PORT}")
STATIC_DIR = Path(__file__).resolve().parent / "static"
SITES_COMPLETE = config.RAW / "ctgov_sites" / "_complete.json"  # written by `make sites`; holds fetched_at
MAX_BODY_BYTES = 4096  # a 300-character question fits many times over

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https://tiles.openfreemap.org; font-src 'self'; "
       "connect-src 'self' https://tiles.openfreemap.org; worker-src 'self' blob:; child-src blob:; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
_RAW_HEADERS = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in SECURITY_HEADERS.items()]
_RAW_NAMES = {k for k, _ in _RAW_HEADERS}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# With nosniff, a script or stylesheet served under a wrong type is blocked, so pin the types the page uses
# rather than trusting whatever mime.types file this machine has.
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
                    (".json", "application/json"), (".svg", "image/svg+xml"), (".wasm", "application/wasm"),
                    (".woff2", "font/woff2"), (".html", "text/html")):
    mimetypes.add_type(_type, _ext)


class ApiError(Exception):
    def __init__(self, status: int, kind: str, message: str, **extra):
        super().__init__(message)
        self.status, self.kind, self.message, self.extra = status, kind, message, extra


def _default(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def json_response(payload, status: int = 200, headers: dict | None = None) -> Response:
    """JSON without FastAPI's per-field encoder, which is slow for 10,000+ GeoJSON features."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_default)
    return Response(body, status_code=status, media_type="application/json", headers=headers)


def error_response(status: int, kind: str, message: str, headers: dict | None = None, **extra) -> Response:
    return json_response({"error": message, "kind": kind, **extra}, status, headers)


class Guard:
    """Pure ASGI middleware, added last so it wraps everything else in the app."""

    def __init__(self, app, allowed_hosts: Iterable[str]):
        self.app = app
        self.hosts = frozenset(h.lower() for h in allowed_hosts)
        self.origins = frozenset(f"http://{h}" for h in self.hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":  # there are no websocket routes: refuse before routing
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":  # lifespan
            await self.app(scope, receive, send)
            return
        started = False

        async def send_with_headers(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in _RAW_NAMES]
                message = {**message, "headers": kept + _RAW_HEADERS}
            await send(message)

        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        if len(hosts) != 1 or hosts[0].lower() not in self.hosts:
            await error_response(400, "bad_host", "Host header not allowed")(scope, receive, send_with_headers)
            return
        origin = headers.get("origin")
        if scope["method"] not in SAFE_METHODS and origin is not None and origin.lower() not in self.origins:
            await error_response(403, "bad_origin", "cross-origin requests are refused")(
                scope, receive, send_with_headers)
            return
        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            log.exception("unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if not started:
                await error_response(500, "server_error", "internal server error")(scope, receive, send_with_headers)


def sites_fetched_at(path: Path) -> str | None:
    """When the site pages were downloaded, as UTC ISO 8601 with Z; None before `make sites` has run."""
    try:
        stamp = dt.datetime.fromisoformat(json.loads(path.read_text())["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Globe:
    """Server state: the one read-only connection, the as-of date, caches and the one-question-at-a-time lock."""

    def __init__(self, db_path: Path, as_of: dt.date):
        self.as_of = as_of
        self._con = sqlguard.sandbox_connection(db_path)
        self._cursor_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._meta: dict | None = None
        self._schema: str | None = None
        self.ask_lock = threading.Lock()

    @contextmanager
    def cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self._cursor_lock:
            cur = self._con.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def meta(self) -> dict:
        # The warehouse cannot change while this read-only connection is open, so compute once.
        with self._cache_lock:
            if self._meta is None:
                with self.cursor() as cur:
                    body = queries.meta(cur, self.as_of)
                self._meta = {"as_of": body.pop("as_of"), "sites_fetched_at": sites_fetched_at(SITES_COMPLETE),
                              **body}
            return self._meta

    def schema(self) -> str:
        with self._cache_lock:
            if self._schema is None:
                with self.cursor() as cur:
                    self._schema = asker.schema_text(cur)
            return self._schema

    def close(self) -> None:
        self._con.close()


async def _read_json_body(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(400, "bad_question", 'send {"question": "..."} as application/json')
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        raise ApiError(400, "bad_question", "request body is too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(400, "bad_question", "request body is too large")
    try:
        data = json.loads(bytes(body))
    except ValueError:
        raise ApiError(400, "bad_question", "request body is not valid JSON") from None
    if not isinstance(data, dict) or set(data) != {"question"}:
        raise ApiError(400, "bad_question", 'send exactly {"question": "..."}')
    return data


def create_app(db_path=None, static_dir=None, allowed_hosts: Iterable[str] | None = None) -> FastAPI:
    """The Readout Globe app. allowed_hosts defaults to 127.0.0.1:8091 and localhost:8091; tests pass their own."""
    as_of = dt.date.fromisoformat(config.as_of_date())
    asker.configure_llm()
    globe = Globe(Path(db_path) if db_path else config.WAREHOUSE_DB, as_of)
    with globe.cursor() as cur:
        missing = queries.missing_tables(cur)
    if missing:
        globe.close()
        raise RuntimeError(f"the warehouse has no {', '.join(missing)}; run `make sites` first")

    @asynccontextmanager
    async def lifespan(_app):
        yield
        globe.close()

    app = FastAPI(title="Readout Globe", docs_url=None, redoc_url=None, openapi_url=None,
                  swagger_ui_oauth2_redirect_url=None, lifespan=lifespan)
    app.state.globe = globe

    @app.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return error_response(exc.status, exc.kind, exc.message, **exc.extra)

    @app.exception_handler(asker.AskError)
    async def _ask_error(_request, exc: asker.AskError):
        extra = {"sql": exc.sql} if exc.sql is not None else {}
        return error_response(exc.status, exc.kind, exc.message, **extra)

    @app.exception_handler(queries.BadFilter)
    async def _bad_filter(_request, exc: queries.BadFilter):
        return error_response(400, "bad_filter", str(exc))

    @app.exception_handler(duckdb.InterruptException)
    async def _timeout(_request, _exc):
        return error_response(504, "timeout", "the query took too long")

    @app.exception_handler(duckdb.Error)
    async def _db_error(request, exc):
        log.error("query failed on %s: %s", request.url.path, str(exc).splitlines()[0][:300] if str(exc) else exc)
        return error_response(500, "query_failed", "the query failed")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request, exc: StarletteHTTPException):
        kinds = {404: ("not_found", "not found"), 405: ("method_not_allowed", "method not allowed")}
        kind, message = kinds.get(exc.status_code, ("http_error", str(exc.detail)))
        return error_response(exc.status_code, kind, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request, _exc):
        return error_response(400, "bad_request", "the request is not valid")

    @app.get("/api/meta")
    def meta():
        return json_response(globe.meta())

    @app.get("/api/points")
    def points(request: Request):
        filters = queries.parse_filters(request.query_params.multi_items())
        with globe.cursor() as cur:
            return json_response(queries.points(cur, filters, globe.as_of))

    @app.get("/api/points/{geo_key}/trials")
    def point_trials(geo_key: str, request: Request):
        key = queries.check_geo_key(geo_key)
        filters = queries.parse_filters(request.query_params.multi_items())
        with globe.cursor() as cur:
            body = queries.point_trials(cur, key, filters, globe.as_of)
        if body is None:
            raise ApiError(404, "not_found", "no such point")
        return json_response(body)

    @app.get("/api/trial/{nct_id}")
    def trial(nct_id: str):
        key = queries.check_nct(nct_id)
        with globe.cursor() as cur:
            body = queries.trial(cur, key, globe.as_of)
        if body is None:
            raise ApiError(404, "not_found", "no such trial in Readout")
        return json_response(body)

    @app.get("/api/orgs")
    def orgs(request: Request):
        q = queries.parse_org_query(request.query_params.multi_items())
        with globe.cursor() as cur:
            return json_response(queries.orgs(cur, q))

    @app.post("/api/ask")
    async def ask(request: Request):
        question = asker.check_question((await _read_json_body(request)).get("question"))
        if not globe.ask_lock.acquire(blocking=False):
            raise ApiError(429, "busy", "another question is being answered; try again when it finishes")
        try:
            body = await run_in_threadpool(
                lambda: asker.answer(question, globe.cursor, globe.schema(), globe.as_of.isoformat()))
        finally:
            globe.ask_lock.release()
        return json_response(body)

    @app.get("/api/{rest:path}")
    def unknown_api(rest: str):
        raise ApiError(404, "not_found", "no such API endpoint")

    static = Path(static_dir) if static_dir else STATIC_DIR
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")

    app.add_middleware(Guard, allowed_hosts=ALLOWED_HOSTS if allowed_hosts is None else tuple(allowed_hosts))
    return app
