import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_dev_rehearsal_phases_are_incremental():
    phases = server.build_dev_rehearsal_phases()

    assert len(phases) == 3
    assert "add" in phases[0]["task"]
    assert "list" in phases[0]["task"]
    assert "delete" in phases[1]["task"]
    assert "search" in phases[2]["task"]
    assert "summary" in phases[2]["task"]


def test_dev_rehearsal_runs_next_phase_only_after_approval(monkeypatch, tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.AUTH_FILE = tmp_path / "auth.json"
    server.WORKSPACE_FILE = tmp_path / "workspace.json"
    server.save_workspace_root(tmp_path)
    emitted = []
    initialized_tasks = []
    reviewed_rounds = []

    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "emit_text_stream", lambda model, role, text, delay=0.0: emitted.append(("stream", {"model": model, "role": role, "text": text})))
    monkeypatch.setattr(server, "init_task", lambda task, max_round=1: initialized_tasks.append(task))
    monkeypatch.setattr(server, "_pipeline_stage_plan", lambda round_num, max_round: None)
    monkeypatch.setattr(server, "_pipeline_stage_build", lambda round_num, max_round: None)
    monkeypatch.setattr(server, "_pipeline_stage_test", lambda round_num, max_round: None)

    def fake_review(round_num, max_round):
        reviewed_rounds.append(round_num)
        return server.PIPELINE_APPROVED if round_num < 2 else server.PIPELINE_RUNNING

    monkeypatch.setattr(server, "_pipeline_stage_review", fake_review)

    server._run_dev_rehearsal(rounds_count=3)

    assert len(initialized_tasks) == 2
    assert reviewed_rounds == [1, 2]
    assert any(event == "step_done" and "第 2 轮未通过" in data["summary"] for event, data in emitted)


def test_dev_rehearsal_api_requires_cli_auth(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.AUTH_FILE = tmp_path / "auth.json"
    server.save_auth({"mode": "deny"})
    client = server.app.test_client()

    response = client.post("/api/dev-rehearsal", json={})

    assert response.status_code == 403
    assert response.get_json()["error"] == "authorization_required"
