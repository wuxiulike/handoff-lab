import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_auth_defaults_to_ask_when_no_file(tmp_path):
    server.AUTH_FILE = tmp_path / "auth.json"

    assert server.load_auth()["mode"] == "ask"


def test_auth_api_updates_mode(tmp_path):
    server.AUTH_FILE = tmp_path / "auth.json"
    client = server.app.test_client()

    response = client.post("/api/auth", json={"mode": "yolo"})

    assert response.status_code == 200
    assert response.get_json()["mode"] == "yolo"
    assert server.load_auth()["mode"] == "yolo"


def test_start_requires_allow_or_yolo_authorization(tmp_path):
    server.AUTH_FILE = tmp_path / "auth.json"
    server.save_auth({"mode": "deny"})
    client = server.app.test_client()

    response = client.post("/api/start", json={"task": "test task"})

    assert response.status_code == 403
    assert response.get_json()["error"] == "authorization_required"
