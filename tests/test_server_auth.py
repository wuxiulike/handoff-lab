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


def test_start_in_ask_mode_creates_pending_auth_request(tmp_path):
    server.AUTH_FILE = tmp_path / "auth.json"
    server.WORKSPACE_FILE = tmp_path / "workspace.json"
    server.save_auth({"mode": "ask"})
    server.save_workspace_root(tmp_path)
    server._pending_start_request = None
    server._event_queue.clear()
    client = server.app.test_client()

    response = client.post("/api/start", json={"task": "修复真实对话按钮无响应问题"})

    assert response.status_code == 403
    assert response.get_json()["error"] == "authorization_required"
    assert server._pending_start_request["task"] == "修复真实对话按钮无响应问题"
    assert any(event["event"] == "auth_request" for event in server._event_queue)


def test_pending_start_rejects_invalid_decision(tmp_path):
    server.AUTH_FILE = tmp_path / "auth.json"
    server._pending_start_request = {
        "task": "修复真实对话按钮无响应问题",
        "max_round": 1,
        "direct_reasonix": True,
    }
    client = server.app.test_client()

    response = client.post("/api/auth/pending-start", json={"decision": "deny"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_auth_decision"
