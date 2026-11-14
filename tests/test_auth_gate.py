"""T16 demo passphrase gate."""
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

PASS = "correct horse"


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setenv("DEMO_PASSPHRASE", PASS)
    auth._fails.clear()
    yield TestClient(app, base_url="https://testserver")  # cookie is Secure
    auth._fails.clear()


def test_gate_off_when_unset():
    c = TestClient(app)
    assert c.get("/orders").status_code == 200
    assert c.get("/auth/status").json() == {"unlocked": True, "gate": False}


def test_401_without_session(gated):
    for path in ("/orders", "/catalog/search?q=milk", "/audit/verify", "/docs", "/openapi.json"):
        r = gated.get(path)
        assert r.status_code == 401, path
        assert r.json()["detail"]
    assert gated.post("/experience/live-token").status_code == 401
    assert gated.post("/orders/x/approve").status_code == 401


def test_open_paths(gated):
    assert gated.get("/health").json() == {"ok": True}
    assert gated.get("/auth/status").json() == {"unlocked": False, "gate": True}
    assert gated.get("/game/").status_code == 200
    assert gated.get("/vimo/").status_code == 200
    assert gated.options("/orders", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}).status_code == 200


def test_unlock_wrong_then_right_and_cookie(gated):
    r = gated.post("/auth/unlock", json={"passphrase": "nope"})
    assert r.status_code == 401 and "aimo_demo" not in gated.cookies
    r = gated.post("/auth/unlock", json={"passphrase": PASS})
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert all(x in sc for x in ("httponly", "secure", "samesite=lax", "path=/", "max-age=604800"))
    assert gated.get("/orders").status_code == 200
    assert gated.get("/auth/status").json()["unlocked"] is True
    gated.post("/auth/lock")
    assert gated.get("/orders").status_code == 401


def test_forged_or_expired_token_rejected(gated):
    gated.cookies.set("aimo_demo", "9999999999.deadbeef")
    assert gated.get("/orders").status_code == 401
    assert not auth.token_ok(PASS, auth.make_token(PASS, now=0))  # expired after 7 days
    assert not auth.token_ok(PASS, auth.make_token("other passphrase"))
    assert auth.token_ok(PASS, auth.make_token(PASS))


def test_header_works(gated):
    assert gated.get("/orders", headers={"X-Demo-Pass": PASS}).status_code == 200
    assert gated.get("/orders", headers={"X-Demo-Pass": "nope"}).status_code == 401


def test_throttle_counts_unlock_and_header_failures(gated):
    for i in range(auth.FAIL_LIMIT):
        if i % 2:
            gated.post("/auth/unlock", json={"passphrase": "nope"})
        else:
            gated.get("/orders", headers={"X-Demo-Pass": "nope"})
    assert gated.post("/auth/unlock", json={"passphrase": PASS}).status_code == 429
    assert gated.get("/orders", headers={"X-Demo-Pass": PASS}).status_code == 429


def test_unlock_works_when_gate_off():
    c = TestClient(app, base_url="https://testserver")
    assert c.post("/auth/unlock", json={"passphrase": "anything"}).status_code == 200
    assert not auth.token_ok(PASS, c.cookies.get("aimo_demo"))  # useless once a gate is enabled


def test_throttle_is_per_client_with_global_cap(gated, monkeypatch):
    for _ in range(auth.FAIL_LIMIT):
        gated.post("/auth/unlock", json={"passphrase": "nope"}, headers={"X-Forwarded-For": "1.1.1.1"})
    assert gated.post("/auth/unlock", json={"passphrase": PASS}, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert gated.post("/auth/unlock", json={"passphrase": PASS}, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    monkeypatch.setattr(auth, "GLOBAL_FAIL_LIMIT", auth.FAIL_LIMIT)  # rotating spoofed IPs still hit the global cap
    assert gated.post("/auth/unlock", json={"passphrase": PASS}, headers={"X-Forwarded-For": "3.3.3.3"}).status_code == 429


def test_demo_gate_off_opens_the_api_even_with_a_passphrase(monkeypatch):
    monkeypatch.setenv("DEMO_PASSPHRASE", PASS)
    monkeypatch.setenv("DEMO_GATE", "off")
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get("/orders").status_code == 200
