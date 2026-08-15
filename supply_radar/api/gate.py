"""A single access code in front of the whole site.

The console is deployed publicly so the panel can open it from a link, without
GitHub accounts, VPNs or an invitation flow. That convenience is the point, and
it also means anyone who guesses the URL is inside. This closes that.

What this is NOT: it is one shared 5-digit code, so it is a door, not a
security model. 100,000 combinations is brute-forceable by anything determined,
which is why failed attempts are rate-limited per client and why nothing behind
this door is sensitive: the discovery data is public business listings and the
supplier list is synthetic. The honest description is "keeps the uninvited out
for a week", and it should not be described as more than that.

The cookie is signed rather than set to the code itself. A cookie holding the
literal code hands it to anyone who opens developer tools on a shared laptop,
and the code also authorises spending money.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "sr_access"
COOKIE_MAX_AGE = 60 * 60 * 12  # a working day; the demo does not outlive it

# Open without the cookie: the entry page itself, the form it posts to, the
# liveness probe Cloud Run calls, and the assets the entry page needs to render.
OPEN_PATHS = {"/enter", "/api/enter", "/api/healthz", "/favicon.ico"}
OPEN_PREFIXES = ("/static/app.css", "/static/vendor/")

# Per-client attempt tracking. In-process and therefore per-instance, which is
# weak on a service that can scale out. Stated rather than hidden: it raises the
# cost of guessing from trivial to tedious, and a demo does not warrant Redis.
_ATTEMPTS: dict[str, list[float]] = {}
MAX_ATTEMPTS = 10
ATTEMPT_WINDOW = 300.0


def _sign(code: str, secret: str) -> str:
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()[:32]


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(request: Request) -> bool:
    """True when this client has failed too often recently."""
    now = time.time()
    key = _client(request)
    recent = [t for t in _ATTEMPTS.get(key, []) if now - t < ATTEMPT_WINDOW]
    _ATTEMPTS[key] = recent
    return len(recent) >= MAX_ATTEMPTS


def record_failure(request: Request) -> None:
    _ATTEMPTS.setdefault(_client(request), []).append(time.time())


def is_open(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


def has_valid_cookie(request: Request, code: str, secret: str) -> bool:
    supplied = request.cookies.get(COOKIE)
    return bool(supplied) and hmac.compare_digest(supplied, _sign(code, secret))


def grant(response, code: str, secret: str, secure: bool = True) -> None:
    """Set the session cookie.

    `secure` follows the request scheme rather than being hardcoded. Cloud Run
    is always https so it is set there, but a browser silently discards a
    secure cookie over http, which would make a locally-run instance with a
    code configured impossible to get into and give no clue why.
    """
    response.set_cookie(
        COOKIE,
        _sign(code, secret),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def is_https(request: Request) -> bool:
    # Cloud Run terminates TLS at the front end, so the app sees http and must
    # read the forwarded scheme to know what the browser actually used.
    forwarded = request.headers.get("x-forwarded-proto", "")
    return (forwarded.split(",")[0].strip() or request.url.scheme) == "https"


def deny(request: Request):
    """An unauthenticated request, answered in the caller's own terms.

    An API call gets 401 JSON, because redirecting XHR to an HTML login page
    produces a parse error rather than a usable message. A browser navigation
    gets the entry page.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Access code required"}, status_code=401)
    return RedirectResponse("/enter", status_code=302)


ENTRY_PAGE = """<!doctype html>
<html lang="en-GB"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Supply Radar</title>
<link rel="stylesheet" href="/static/app.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='26' font-size='26'>&#128225;</text></svg>">
<style>
  body { display:grid; place-items:center; min-height:100vh; margin:0; }
  .gate { width:min(360px, calc(100vw - 40px)); text-align:center; }
  .gate h1 { font-size:19px; margin:0 0 6px; }
  .gate p { color:var(--muted); font-size:13.5px; margin:0 0 18px; }
  .gate input {
    width:100%; text-align:center; font-family:var(--mono); font-size:26px;
    letter-spacing:.36em; padding:12px 0; margin-bottom:10px;
  }
  .gate .err { color:var(--bad); font-size:13px; min-height:18px; margin-top:10px; }
</style>
</head><body>
<form class="gate" id="f">
  <div style="font-size:30px;margin-bottom:10px">&#128225;</div>
  <h1>Supply Radar</h1>
  <p>Enter the 5-digit code to continue.</p>
  <input id="c" inputmode="numeric" autocomplete="off" maxlength="5"
         pattern="[0-9]*" placeholder="&middot;&middot;&middot;&middot;&middot;" autofocus>
  <button class="btn" style="width:100%" type="submit">Continue</button>
  <div class="err" id="e"></div>
</form>
<script>
const f=document.getElementById('f'), c=document.getElementById('c'), e=document.getElementById('e');
c.oninput = () => { e.textContent=''; if (c.value.length===5) f.requestSubmit(); };
f.onsubmit = async (ev) => {
  ev.preventDefault();
  const res = await fetch('/api/enter', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({code: c.value.trim()}),
  });
  if (res.ok) { location.href='/'; return; }
  const body = await res.json().catch(() => ({}));
  e.textContent = body.detail || 'That code was not recognised.';
  c.value=''; c.focus();
};
</script>
</body></html>
"""


def entry_page() -> HTMLResponse:
    return HTMLResponse(ENTRY_PAGE, headers={"Cache-Control": "no-cache"})
