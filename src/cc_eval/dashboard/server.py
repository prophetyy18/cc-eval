"""The cc-eval console: a local web application over the workspace.

What it is: a **console**, not a website.  It authors test cases, starts
experiments, streams the results as they happen, and shows the history.

The security posture is deliberate, because the console can be exposed to the
internet and it can *read* run records, *write* case files and *start* real
agent runs:

* **Authentication is mandatory when the console is not bound to loopback.**
  Without a token the server refuses to start - it fails closed.
* **Reading and writing are separate roles.**  The write endpoints (authoring
  a case, starting a run) need the admin token and ``--allow-write``.
* **The framework paths of a run request are validated** against the allowed
  roots, so a caller cannot point the agent at an arbitrary directory.
* **Every response carries the usual hardening headers**, and the session
  cookie is ``HttpOnly`` + ``SameSite=Strict`` with a signed, expiring value.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.config import EvalPaths
from ..core.registry import Registry
from .authoring import (
    AuthoringError,
    delete_case,
    list_cases,
    write_case,
)
from .runs import RunManager, RunRequestError
from .types import all_types
from .types import get as get_test_type

COOKIE_NAME = "cc_eval_session"
SESSION_TTL_SECONDS = 12 * 3600
MAX_BODY_BYTES = 1_000_000
LOGIN_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300

_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AuthError(Exception):
    """Raised when a request is not allowed.  It aborts the handler: a
    request must produce exactly one response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ConsoleConfig:
    """How the console is exposed."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 30000,
        viewer_token: str = "",
        admin_token: str = "",
        allow_write: bool = False,
        tls_cert: str = "",
        tls_key: str = "",
        allowed_framework_roots: list[Path] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.viewer_token = viewer_token
        self.admin_token = admin_token
        self.allow_write = allow_write
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.allowed_framework_roots = allowed_framework_roots or []


@dataclass
class _Session:
    role: str
    expires_at: float


class ConsoleServer:
    """The HTTP server and its state."""

    def __init__(self, paths: EvalPaths, config: ConsoleConfig) -> None:
        self.paths = paths
        self.config = config
        self.secret = secrets.token_bytes(32)
        self.registry = Registry(paths.registry)
        self.manager = RunManager(
            paths, allowed_framework_roots=config.allowed_framework_roots
        )
        self._login_failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

        self._validate_startup()

    # -- startup guards ---------------------------------------------------- #
    def _validate_startup(self) -> None:
        config = self.config
        loopback = config.host in {"127.0.0.1", "::1", "localhost"}
        if not loopback and not (config.viewer_token or config.admin_token):
            raise SystemExit(
                f"refusing to bind {config.host} without a token: set "
                "CC_EVAL_DASHBOARD_TOKEN (read) or CC_EVAL_DASHBOARD_ADMIN_TOKEN, "
                "or start on 127.0.0.1"
            )
        if config.allow_write and not config.admin_token:
            raise SystemExit(
                "refusing to enable writing without an admin token: set "
                "CC_EVAL_DASHBOARD_ADMIN_TOKEN"
            )

    # -- sessions ---------------------------------------------------------- #
    def _sign(self, payload: bytes) -> str:
        return hmac.new(self.secret, payload, sha256).hexdigest()

    def issue(self, role: str) -> str:
        body = json.dumps({"role": role, "exp": time.time() + SESSION_TTL_SECONDS}).encode()
        encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
        return f"{encoded}.{self._sign(encoded.encode())}"

    def read_session(self, cookie: str) -> _Session | None:
        if not cookie or "." not in cookie:
            return None
        encoded, signature = cookie.rsplit(".", 1)
        if not hmac.compare_digest(self._sign(encoded.encode()), signature):
            return None
        try:
            padding = "=" * (-len(encoded) % 4)
            data = json.loads(base64.urlsafe_b64decode(encoded + padding))
        except (ValueError, json.JSONDecodeError):
            return None
        if float(data.get("exp", 0)) < time.time():
            return None
        return _Session(role=str(data.get("role", "viewer")), expires_at=float(data["exp"]))

    def check_token(self, token: str) -> str:
        """The role a token grants."""
        admin = self.config.admin_token
        viewer = self.config.viewer_token
        if admin and hmac.compare_digest(token, admin):
            return "admin"
        if viewer and hmac.compare_digest(token, viewer):
            return "viewer"
        if admin and hmac.compare_digest(token, admin):
            return "admin"
        raise PermissionError("invalid token")

    def too_many_attempts(self, client: str) -> bool:
        now = time.time()
        with self._lock:
            attempts = [t for t in self._login_failures.get(client, []) if now - t < LOGIN_WINDOW_SECONDS]
            self._login_failures[client] = attempts
            return len(attempts) >= LOGIN_ATTEMPTS

    def record_failure(self, client: str) -> None:
        with self._lock:
            self._login_failures.setdefault(client, []).append(time.time())

    # -- http -------------------------------------------------------------- #
    def serve_forever(self) -> None:
        handler = _make_handler(self)
        server = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        server.daemon_threads = True
        if self.config.tls_cert and self.config.tls_key:
            import ssl

            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.config.tls_cert, self.config.tls_key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        host = self.config.host
        scheme = "https" if self.config.tls_cert else "http"
        print(f"cc-eval console on {scheme}://{host}:{self.config.port}", flush=True)
        if self.config.admin_token:
            print("  admin token: configured (authoring + runs enabled)", flush=True)
        elif self.config.viewer_token:
            print("  read-only: no admin token configured", flush=True)
        else:
            print("  no token: loopback only", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:  # pragma: no cover - interactive
            print("\nstopping", flush=True)
        finally:
            server.server_close()


def _make_handler(server: ConsoleServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "cc-eval-console"
        sys_version = ""

        # -- plumbing ------------------------------------------------------ #
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            print(f"[console] {self.address_string()} {fmt % args}", flush=True)

        def _headers(self, content_type: str, length: int, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._headers("application/json; charset=utf-8", len(body), status)
            self.wfile.write(body)

        def _text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
            body = text.encode("utf-8")
            self._headers(content_type, len(body), status)
            self.wfile.write(body)

        def _cookie(self) -> str:
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE_NAME:
                    return value
            return ""

        def _session(self) -> _Session | None:
            return server.read_session(self._cookie())

        def _require(self, role: str = "viewer") -> _Session:
            session = self._session()
            if session is None:
                raise AuthError(401, "not authenticated")
            if role == "admin" and session.role != "admin":
                raise AuthError(403, "admin token required")
            return session

        def _read_body(self) -> None:
            """Read the request body *before* answering.

            A response that is written while the client is still sending its
            body resets the connection, so the body is always drained first.
            """
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise ValueError("request body too large")
            self._raw_body = self.rfile.read(length) if length else b"{}"

        def _body(self) -> dict[str, Any]:
            raw = getattr(self, "_raw_body", b"{}")
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("the request body must be a JSON object")
            return data

        def _wants_json(self) -> bool:
            return "application/json" in (self.headers.get("Content-Type") or "")

        # -- routing -------------------------------------------------------- #
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path in {"/", "/index.html"}:
                    self._serve_console()
                elif path == "/api/session":
                    session = self._session()
                    self._json(
                        {
                            "authenticated": session is not None,
                            "role": session.role if session else "",
                            "can_write": bool(session and session.role == "admin"),
                        }
                    )
                elif path == "/api/test-types":
                    self._require()
                    self._json({"test_types": [t.as_dict() for t in all_types()]})
                elif path.startswith("/api/test-types/"):
                    self._cases_get(path)
                elif path == "/api/experiments":
                    self._require()
                    self._json({"experiments": server.registry.list_experiments()})
                elif path == "/api/runs":
                    self._require()
                    self._json({"runs": server.manager.handles()})
                elif path.startswith("/api/runs/"):
                    self._run_get(path)
                elif path.startswith("/reports/"):
                    self._serve_report(path)
                else:
                    self._json({"error": "not found"}, 404)
            except AuthError as exc:
                self._json({"error": exc.message}, exc.status)
            except PermissionError as exc:
                self._json({"error": str(exc)}, 403)
            except Exception as exc:  # noqa: BLE001 - never leak a traceback
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                self._read_body()
                if path == "/api/login":
                    self._login()
                elif path == "/api/logout":
                    self._logout()
                elif path.startswith("/api/test-types/"):
                    self._cases_post(path)
                elif path == "/api/runs":
                    self._run_start()
                else:
                    self._json({"error": "not found"}, 404)
            except AuthError as exc:
                self._json({"error": exc.message}, exc.status)
            except PermissionError as exc:
                self._json({"error": str(exc)}, 403)
            except (ValueError, AuthoringError, RunRequestError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                self._read_body()
                if path.startswith("/api/test-types/"):
                    self._cases_delete(path)
                else:
                    self._json({"error": "not found"}, 404)
            except AuthError as exc:
                self._json({"error": exc.message}, exc.status)
            except PermissionError as exc:
                self._json({"error": str(exc)}, 403)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        # -- endpoints ------------------------------------------------------ #
        def _login(self) -> None:
            client = self.client_address[0] if self.client_address else "?"
            if server.too_many_attempts(client):
                self._json({"error": "too many failed attempts; try again later"}, 429)
                return
            data = self._body()
            token = str(data.get("token") or "")
            try:
                role = server.check_token(token)
            except PermissionError:
                server.record_failure(client)
                self._json({"error": "invalid token"}, 401)
                return
            cookie = f"{COOKIE_NAME}={server.issue(role)}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(json.dumps({"role": role}).encode("utf-8"))

        def _logout(self) -> None:
            cookie = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def _cases_get(self, path: str) -> None:
            self._require()
            parts = [p for p in path.split("/") if p]
            name = parts[2] if len(parts) > 2 else ""
            test_type = get_test_type(name)
            self._json({"cases": list_cases(test_type)})

        def _cases_post(self, path: str) -> None:
            self._require("admin")
            parts = [p for p in path.split("/") if p]
            name = parts[2] if len(parts) > 2 else ""
            test_type = get_test_type(name)
            data = self._body()
            values = data.get("values")
            if not isinstance(values, dict):
                raise ValueError("values must be an object")
            case = write_case(
                test_type,
                values,
                case_id=str(data.get("case_id") or "") or None,
            )
            self._json({"case": case})

        def _cases_delete(self, path: str) -> None:
            self._require("admin")
            parts = [p for p in path.split("/") if p]
            name = parts[2] if len(parts) > 2 else ""
            case_id = parts[4] if len(parts) > 4 else ""
            test_type = get_test_type(name)
            removed = delete_case(test_type, case_id)
            self._json({"deleted": removed})

        def _run_start(self) -> None:
            self._require("admin")
            if not server.config.allow_write:
                raise PermissionError("the console is read-only")
            data = self._body()
            handle = server.manager.start(data)
            self._json({"run_id": handle.id, "status": handle.status})

        def _run_get(self, path: str) -> None:
            self._require()
            parts = [p for p in path.split("/") if p]
            run_id = parts[3] if len(parts) > 3 else ""
            handle = server.manager.get(run_id)
            if handle is None:
                self._json({"error": "unknown run"}, 404)
                return
            if parts[4:5] == ["events"]:
                self._stream(handle)
                return
            self._json(
                {
                    "id": handle.id,
                    "status": handle.status,
                    "error": handle.error,
                    "results": handle.results,
                    "events": handle.events,
                }
            )

        def _stream(self, handle: Any) -> None:
            subscriber = server.manager.subscribe(handle.id)
            if subscriber is None:
                self._json({"error": "unknown run"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                while True:
                    try:
                        event = subscriber.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    payload = json.dumps(event, ensure_ascii=False, default=str)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    if event.get("type") in {"experiment_finished", "experiment_failed"}:
                        break
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                return

        def _serve_console(self) -> None:
            html = (Path(__file__).parent / "templates" / "console.html").read_text(
                encoding="utf-8"
            )
            self._text(html, content_type="text/html; charset=utf-8")

        def _serve_report(self, path: str) -> None:
            self._require()
            relative = path[len("/reports/") :]
            parts = relative.split("/")
            if len(parts) != 2 or not all(_ARTIFACT_RE.match(p) for p in parts):
                self._json({"error": "invalid report path"}, 400)
                return
            experiment_id, filename = parts
            if filename not in {"report.html", "report.json", "report.md"}:
                self._json({"error": "not found"}, 404)
                return
            target = (server.paths.reports / experiment_id / filename).resolve()
            root = server.paths.reports.resolve()
            if not str(target).startswith(str(root)) or not target.exists():
                self._json({"error": "not found"}, 404)
                return
            body = target.read_bytes()
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".md": "text/markdown; charset=utf-8",
            }[target.suffix]
            self._headers(content_type, len(body))
            self.wfile.write(body)

    return Handler


def tokens_from_env() -> tuple[str, str]:
    """The tokens the console accepts, from the environment."""
    return (
        os.environ.get("CC_EVAL_DASHBOARD_TOKEN", "").strip(),
        os.environ.get("CC_EVAL_DASHBOARD_ADMIN_TOKEN", "").strip(),
    )
