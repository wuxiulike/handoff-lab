import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_history_api_delete_clears_turns_and_events(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.append_session_turn("user", "hello")
    server.append_session_event("event", "summary")

    client = server.app.test_client()
    response = client.delete("/api/history")

    assert response.status_code == 200
    assert response.get_json()["status"] == "cleared"
    assert server.load_session()["turns"] == []
    assert server.load_session()["events"] == []


def test_history_api_delete_single_conversation_by_index(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})
    server.append_session_turn("user", "第一条")
    server.create_conversation(workspace=str(tmp_path))
    server.append_session_turn("user", "第二条")

    client = server.app.test_client()
    response = client.delete("/api/history/0")

    assert response.status_code == 200
    assert response.get_json()["status"] == "deleted"
    conversations = server.build_history_conversations()
    assert len(conversations) == 1
    assert conversations[0]["text"] == "第一条"


def test_history_api_delete_single_conversation_rejects_bad_index(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})

    client = server.app.test_client()
    response = client.delete("/api/history/9")

    assert response.status_code == 404
    assert response.get_json()["error"] == "history_conversation_not_found"


def test_history_api_returns_active_session_and_conversations(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.append_session_turn("user", "说段相声：同学录")

    client = server.app.test_client()
    response = client.get("/api/history")

    assert response.status_code == 200
    data = response.get_json()
    assert data["turns"][0]["text"] == "说段相声：同学录"
    assert data["conversations"][0]["text"] == "说段相声：同学录"


def test_history_activate_switches_context_and_workspace(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.WORKSPACE_FILE = tmp_path / "workspace.json"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"

    server.save_workspace_root(first_workspace)
    server.append_session_turn("user", "first task")
    first_id = server.load_session()["active_id"]

    server.create_conversation(workspace=str(second_workspace))
    server.save_workspace_root(second_workspace)
    server.append_session_turn("user", "second task")

    client = server.app.test_client()
    response = client.post(f"/api/history/{first_id}/activate")

    assert response.status_code == 200
    data = response.get_json()
    assert data["conversation"]["id"] == first_id
    assert data["conversation"]["text"] == "first task"
    assert Path(data["workspace"]) == first_workspace.resolve()
    assert server.load_session()["turns"][0]["text"] == "first task"


def test_history_activate_recovers_workspace_from_conversation_paths(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    recovered_workspace = tmp_path / "recovered"
    outbox = recovered_workspace / ".agent" / "outbox"
    app_root.mkdir()
    outbox.mkdir(parents=True)
    (recovered_workspace / "server.py").write_text("print('ok')", encoding="utf-8")

    monkeypatch.setattr(server, "ROOT", app_root)
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    server.save_workspace_root(app_root)
    session = {
        "mode": "dev_loop",
        "active_id": "old",
        "conversations": [
            {
                "id": "old",
                "title": "旧任务",
                "workspace": str(app_root),
                "created_at": 1,
                "updated_at": 1,
                "turns": [
                    {
                        "role": "user",
                        "text": f"Reasonix wrote {outbox / 'to_reasonix.md'}",
                        "ts": 1,
                        "messages": [],
                    }
                ],
                "events": [],
            }
        ],
    }
    server.save_session(session)

    client = server.app.test_client()
    response = client.post("/api/history/old/activate")

    assert response.status_code == 200
    data = response.get_json()
    assert Path(data["workspace"]) == recovered_workspace.resolve()
    assert Path(server.load_workspace_root()) == recovered_workspace.resolve()
