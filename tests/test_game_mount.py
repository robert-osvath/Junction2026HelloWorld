import importlib
from pathlib import Path

from fastapi.testclient import TestClient

import app.main


def test_game_served_when_present():
    c = TestClient(app.main.app)
    assert c.get("/game/").status_code == 200
    assert c.get("/game/audio/lines.json").json()["lines"]


def test_app_starts_without_game_dir(monkeypatch):
    # prod (Modal image) ships no web/game: the app must still import and simply not mount /game
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    try:
        mod = importlib.reload(app.main)
        assert not any(getattr(r, "path", "") == "/game" for r in mod.app.routes)
        assert TestClient(mod.app).get("/health").json() == {"ok": True}
    finally:
        monkeypatch.undo()
        importlib.reload(app.main)
