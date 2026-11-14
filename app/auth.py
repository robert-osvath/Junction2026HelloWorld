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
FAIL_LIMIT, FAIL_WINDOW = 10, 60.0  # short window, no hard lockout: behind Vercel all users may share one IP
_fails: dict[str, deque] = defaultdict(deque)

router = APIRouter(prefix="/auth", tags=["auth"])


def passphrase() -> str:
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
    return request.client.host if request.client else "?"


def _throttled(ip: str) -> bool:
    q, cutoff = _fails[ip], time.monotonic() - FAIL_WINDOW
    while q and q[0] < cutoff:
        q.popleft()
    return len(q) >= FAIL_LIMIT


def _check_pass(request: Request, candidate: str) -> bool | None:
    """True/False on a checked attempt, None when throttled. Failures count toward the throttle."""
    ip = _ip(request)
    if _throttled(ip):
        return None
    if hmac.compare_digest(candidate.encode(), passphrase().encode()):
        return True
    _fails[ip].append(time.monotonic())
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
def unlock(body: Unlock, request: Request, response: Response) -> dict:
    p = passphrase()
    if p:  # gate off: unlock always succeeds so the web UI flow still works locally
        ok = _check_pass(request, body.passphrase)
        if ok is None:
            return JSONResponse({"detail": "Too many attempts, wait a minute"}, status_code=429)
        if not ok:
            return JSONResponse({"detail": "Wrong passphrase"}, status_code=401)
    response.set_cookie(COOKIE, make_token(p), max_age=MAX_AGE, httponly=True, secure=True, samesite="lax", path="/")
    return {"unlocked": True}


@router.post("/lock")
def lock(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return {"unlocked": False}


@router.get("/status")
def status(request: Request) -> dict:
    return {"unlocked": unlocked(request), "gate": bool(passphrase())}
