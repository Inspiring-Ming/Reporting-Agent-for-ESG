"""Lightweight access control for hosted demo.

Two things:

  1. Shared-password gate. Anyone hitting the site supplies a password once;
     we set a signed cookie. Without ACCESS_PASSWORD env var, gate is disabled
     (local dev convenience).

  2. Per-IP rate limit on the expensive endpoints (LLM + PDF). In-memory
     sliding window — fine for a single Flask process. Restart resets counters.

Apply via decorators on individual routes (see server.py).
"""
from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque
from functools import wraps
from hashlib import sha256
from threading import Lock
from typing import Callable

from flask import jsonify, make_response, render_template_string, request


COOKIE_NAME = "amp_expo_access"
COOKIE_MAX_AGE = 7 * 24 * 3600  # one week


# ---- shared-password gate ----

def _expected_password() -> str | None:
    pw = os.environ.get("ACCESS_PASSWORD", "").strip()
    return pw or None


def _signing_secret() -> str:
    # Defaults to the password itself — fine because we only sign a static
    # marker, not user data. Override with ACCESS_COOKIE_SECRET if you want
    # to rotate cookies independently of the password.
    return (
        os.environ.get("ACCESS_COOKIE_SECRET")
        or _expected_password()
        or "no-password-set"
    )


def _make_token() -> str:
    return hmac.new(
        _signing_secret().encode("utf-8"),
        b"amp_expo_access_v1",
        sha256,
    ).hexdigest()


def _has_valid_cookie() -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    return hmac.compare_digest(token, _make_token())


def is_gate_enabled() -> bool:
    return _expected_password() is not None


def is_authed() -> bool:
    return (not is_gate_enabled()) or _has_valid_cookie()


_LOGIN_HTML = """<!doctype html>
<html><head><title>Axiolytics — sign in</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial;
         background: #f6f7fb; margin: 0; padding: 0;
         display: flex; min-height: 100vh; align-items: center; justify-content: center; }
  .card { background: #fff; padding: 36px 40px; border-radius: 14px;
          box-shadow: 0 6px 24px rgba(0,0,0,0.08); max-width: 380px; width: 90%; }
  h1 { color: #003C71; margin: 0 0 6px; font-size: 22px; }
  p { color: #5b6477; margin: 0 0 18px; font-size: 14px; }
  input { width: 100%; padding: 11px 14px; border: 1px solid #e3e6ed;
          border-radius: 8px; font-size: 15px; }
  input:focus { outline: none; border-color: #0072CE; box-shadow: 0 0 0 3px #e6f1fb; }
  button { width: 100%; margin-top: 12px; background: #003C71; color: #fff;
           border: none; border-radius: 8px; padding: 11px; font-size: 15px;
           font-weight: 600; cursor: pointer; }
  button:hover { background: #002852; }
  .err { color: #c53030; font-size: 13px; margin-top: 10px; }
  .hint { color: #5b6477; font-size: 12px; margin-top: 16px;
          padding-top: 14px; border-top: 1px solid #eef0f5; line-height: 1.5; }
  .hint a { color: #0072CE; text-decoration: none; }
  .hint a:hover { text-decoration: underline; }
</style></head>
<body><form class="card" method="post" action="/login">
  <h1>Axiolytics</h1>
  <p>Enter the access password to continue.</p>
  <input type="password" name="password" autofocus required placeholder="Password" />
  <button type="submit">Enter</button>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <div class="hint">
    Don't have the password? Email
    <a href="mailto:mingchin.yuyu@gmail.com">mingchin.yuyu@gmail.com</a>.
  </div>
</form></body></html>"""


def login_view():
    """GET /login — show the form."""
    if is_authed():
        # Already in. Bounce to root.
        return _redirect("/")
    return render_template_string(_LOGIN_HTML, error=None)


def login_submit():
    """POST /login — verify and set cookie."""
    expected = _expected_password()
    if not expected:
        return _redirect("/")
    submitted = (request.form.get("password") or "").strip()
    if not hmac.compare_digest(submitted, expected):
        return render_template_string(_LOGIN_HTML, error="Incorrect password."), 401
    resp = _redirect("/")
    resp.set_cookie(
        COOKIE_NAME, _make_token(),
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax",
        secure=request.is_secure,
    )
    return resp


def logout_view():
    resp = _redirect("/login")
    resp.delete_cookie(COOKIE_NAME)
    return resp


def _redirect(path: str):
    resp = make_response("", 302)
    resp.headers["Location"] = path
    return resp


def require_auth(fn: Callable):
    """Decorator: enforce the gate (if enabled). HTML routes get a redirect,
    JSON routes get a 401."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if is_authed():
            return fn(*args, **kwargs)
        wants_json = (
            request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
        )
        if wants_json:
            return jsonify({"error": "authentication required", "login_url": "/login"}), 401
        return _redirect("/login?next=" + request.path)
    return wrapped


# ---- per-IP rate limit ----

_RATE_LOCK = Lock()
_RATE_HITS: dict[str, deque] = defaultdict(deque)


def _client_ip() -> str:
    # X-Forwarded-For is sent by Render/Cloudflare/ngrok in front of Flask.
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limit(*, limit: int, per_seconds: int):
    """Decorator factory: at most `limit` calls per `per_seconds` per IP.

    Tied to is_gate_enabled() — when the demo runs without ACCESS_PASSWORD
    (i.e. locally, by you alone), rate limits are off. When hosted with the
    password gate, they kick in."""
    def deco(fn: Callable):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not is_gate_enabled():
                return fn(*args, **kwargs)
            ip = _client_ip()
            now = time.time()
            with _RATE_LOCK:
                hits = _RATE_HITS[ip]
                while hits and hits[0] < now - per_seconds:
                    hits.popleft()
                if len(hits) >= limit:
                    retry_after = int(per_seconds - (now - hits[0])) + 1
                    resp = jsonify({
                        "error": "rate limit exceeded",
                        "limit": limit,
                        "per_seconds": per_seconds,
                        "retry_after_seconds": retry_after,
                    })
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(retry_after)
                    return resp
                hits.append(now)
            return fn(*args, **kwargs)
        return wrapped
    return deco
