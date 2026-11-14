"""Demo passphrase gate (T16). Off when DEMO_PASSPHRASE is unset/empty (local, CI)."""
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.routing import Match, Mount

COOKIE = "aimo_demo"
MAX_AGE = 7 * 24 * 3600
FAIL_LIMIT, FAIL_WINDOW = 10, 60.0  # per client, short window, no hard lockout
GLOBAL_FAIL_LIMIT = 100  # all clients together: caps brute force even when the client key is spoofed
_fails: dict[str, deque] = defaultdict(deque)

router = APIRouter(prefix="/auth", tags=["auth"])


def passphrase() -> str:
    """The demo passphrase; "" turns the gate off. DEMO_GATE=off opens the demo without editing the secret."""
    if os.getenv("DEMO_GATE", "").lower() == "off":
        return ""
    return os.getenv("DEMO_PASSPHRASE") or ""


def _secret(p: str) -> bytes:
    return (os.getenv("DEMO_SESSION_SECRET") or hashlib.sha256(f"aimo-demo-session:{p}".encode()).hexdigest()).encode()


def make_token(p: str, now: float | None = None) -> str:
    exp = str(int((time.time() if now is None else now) + MAX_AGE))
    return f"{exp}.{hmac.new(_secret(p), exp.encode(), hashlib.sha256).hexdigest()}"


def token_ok(p: str, token: str | None, now: float | None = None) -> bool:
    exp, _, sig = (token or "").partition(".")
    if not exp.isdigit() or int(exp) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sig, hmac.new(_secret(p), exp.encode(), hashlib.sha256).hexdigest())


def _ip(request: Request) -> str:
    # behind Vercel + Modal the socket peer is a proxy shared by every visitor; Vercel overwrites
    # X-Forwarded-For with the real client IP. Spoofable on direct calls, hence the global cap too.
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "?")


def _recent(key: str) -> int:
    q, cutoff = _fails[key], time.monotonic() - FAIL_WINDOW
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        del _fails[key]  # keep the dict bounded by clients seen in the last minute
        return 0
    return len(q)


def _throttled(ip: str) -> bool:
    return _recent(ip) >= FAIL_LIMIT or _recent("*") >= GLOBAL_FAIL_LIMIT


def _check_pass(request: Request, candidate: str) -> bool | None:
    """True/False on a checked attempt, None when throttled. Failures count toward the throttle."""
    ip = _ip(request)
    if _throttled(ip):
        return None
    if hmac.compare_digest(candidate.encode(), passphrase().encode()):
        return True
    now = time.monotonic()
    _fails[ip].append(now)
    _fails["*"].append(now)
    return False


def unlocked(request: Request) -> bool:
    p = passphrase()
    return not p or token_ok(p, request.cookies.get(COOKIE))


def _is_open(request: Request) -> bool:
    path = request.url.path
    if request.method == "OPTIONS" or path == "/health" or path.startswith("/auth/"):
        return True
    for route in request.app.router.routes:  # static mounts (/game, /vimo) are open; API routes are not
        if route.matches(request.scope)[0] == Match.FULL:
            return isinstance(route, Mount)
    return True  # no route: the app answers 404/405/slash-redirect, nothing to protect


async def gate(request: Request, call_next):
    if not passphrase() or _is_open(request) or unlocked(request):
        return await call_next(request)
    header = request.headers.get("x-demo-pass")
    if header is not None:
        ok = _check_pass(request, header)
        if ok:
            return await call_next(request)
        if ok is None:
            return JSONResponse({"detail": "Too many attempts, wait a minute"}, status_code=429)
    return JSONResponse({"detail": "Demo passphrase required"}, status_code=401)


class Unlock(BaseModel):
    passphrase: str


@router.post("/unlock")
def unlock(body: Unlock, request: Request, response: Response):
    p = passphrase()
    if p:  # gate off: unlock always succeeds so the web UI flow still works locally
        ok = _check_pass(request, body.passphrase)
        if ok is None:
            return JSONResponse({"detail": "Too many attempts, wait a minute"}, status_code=429)
        if not ok:
            return JSONResponse({"detail": "Wrong passphrase"}, status_code=401)
    # gate off: a placeholder that never validates, so it cannot outlive a later-enabled gate
    response.set_cookie(COOKIE, make_token(p) if p else "open", max_age=MAX_AGE, httponly=True, secure=True, samesite="lax", path="/")
    return {"unlocked": True}


@router.post("/lock")
def lock(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return {"unlocked": False}


@router.get("/status")
def status(request: Request) -> dict:
    return {"unlocked": unlocked(request), "gate": bool(passphrase())}
