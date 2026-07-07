import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class FakeThread:
    created = []

    def __init__(self, target=None, args=(), daemon=False):
        self.target = target
        self.args = args
        self.daemon = daemon
        FakeThread.created.append(self)

    def start(self):
        return None


def test_api_start_direct_reasonix_prepares_packet_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "AUTH_FILE", tmp_path / ".agent" / "auth.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / ".agent" / "session.json")
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / ".agent" / "workspace.json")
    monkeypatch.setattr(server, "CONFIG_FILE", tmp_path / ".agent" / "model_config.json")
    monkeypatch.setattr(server, "QA_WATCH_FILE", tmp_path / ".agent" / "qa_watch.json")
    monkeypatch.setattr(server, "is_authorized_for_pipeline", lambda: True)
    monkeypatch.setattr(server, "is_underspecified_task", lambda task: False)
    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    server.save_workspace_root(tmp_path)
    FakeThread.created.clear()
    client = server.app.test_client()

    response = client.post("/api/start", json={
        "task": "# REASONIX_IMPLEMENTATION_PACKET\n\nDo a tiny smoke test.",
        "max_round": 1,
        "direct_reasonix": True,
    })

    assert response.status_code == 200
    assert response.get_json()["direct_reasonix"] is True
    state = server.load_state()
    assert state["status"] == "WAIT_REASONIX_BUILD"
    assert state["next_actor"] == "reasonix"
    assert state["direct_reasonix"] is True
    assert (tmp_path / ".agent" / "codex_plan.md").exists()
    assert FakeThread.created[-1].args == (1, True)
