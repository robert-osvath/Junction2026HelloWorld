from fastapi.testclient import TestClient

from app.main import GAME_DIR, app


def test_game_served_when_present():
    assert GAME_DIR.is_dir()
    c = TestClient(app)
    assert c.get("/game/").status_code == 200
    assert c.get("/game/audio/lines.json").json()["lines"]
