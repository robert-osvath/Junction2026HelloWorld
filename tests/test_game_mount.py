from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app, mount_game


def test_game_served_when_present():
    c = TestClient(app)
    assert c.get("/game/").status_code == 200
    assert c.get("/game/audio/lines.json").json()["lines"]


def test_missing_game_dir_is_skipped(tmp_path):
    # prod (Modal image) ships no web/: skip the mount instead of failing at import
    fresh = FastAPI()
    assert mount_game(fresh, tmp_path / "nope") is False
    assert not any(getattr(r, "path", "") == "/game" for r in fresh.routes)
