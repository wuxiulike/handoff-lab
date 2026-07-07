import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_start_rejects_vague_short_task(tmp_path):
    server.AUTH_FILE = tmp_path / "auth.json"
    server.save_auth({"mode": "yolo"})
    client = server.app.test_client()

    response = client.post("/api/start", json={"task": "能行吗？"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "task_underspecified"


def test_start_accepts_clear_development_task(tmp_path, monkeypatch):
    server.AUTH_FILE = tmp_path / "auth.json"
    server.SESSION_FILE = tmp_path / "session.json"
    server.save_auth({"mode": "yolo"})
    monkeypatch.setattr(server, "init_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "load_state", lambda: {"task_id": "test"})

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server.threading, "Thread", NoopThread)
    client = server.app.test_client()

    response = client.post("/api/start", json={"task": "修复真实对话按钮无响应问题"})

    assert response.status_code == 200
