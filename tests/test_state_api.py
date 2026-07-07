import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "AUTH_FILE", tmp_path / ".agent" / "auth.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / ".agent" / "session.json")
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / ".agent" / "workspace.json")
    monkeypatch.setattr(server, "CONFIG_FILE", tmp_path / ".agent" / "model_config.json")
    monkeypatch.setattr(server, "QA_WATCH_FILE", tmp_path / ".agent" / "qa_watch.json")
    server.save_workspace_root(tmp_path)


def test_state_api_returns_agent_state_and_build_report(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    server.write_text(".agent/state.json", """{
      "task_id": "task-1",
      "round": 1,
      "max_round": 1,
      "status": "MAX_ROUND_REACHED",
      "approved": false,
      "last_actor": "codex",
      "next_actor": "human"
    }""")
    server.write_text(".agent/build_report.md", "# Build Report\n\nREADY_FOR_CODEX_REVIEW")
    client = server.app.test_client()

    response = client.get("/api/state")

    assert response.status_code == 200
    data = response.get_json()
    assert data["workspace"] == str(tmp_path)
    assert data["running"] is False
    assert data["state"]["status"] == "MAX_ROUND_REACHED"
    assert data["status"] == "MAX_ROUND_REACHED"
    assert data["terminal"] is True
    assert data["build_report"]["exists"] is True
    assert "READY_FOR_CODEX_REVIEW" in data["build_report"]["excerpt"]


def test_health_api_returns_ok(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    client = server.app.test_client()

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_state_api_treats_wait_test_as_terminal_handoff(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    server.write_text(".agent/state.json", """{
      "task_id": "task-2",
      "round": 1,
      "max_round": 1,
      "status": "WAIT_TEST",
      "approved": false,
      "last_actor": "reasonix",
      "next_actor": "orchestrator"
    }""")
    server.write_text(".agent/build_report.md", "# Build Report\n\nREADY_FOR_CODEX_REVIEW")
    client = server.app.test_client()

    response = client.get("/api/state")

    assert response.status_code == 200
    data = response.get_json()
    assert data["running"] is False
    assert data["terminal"] is True
    assert data["status"] == "WAIT_TEST"
