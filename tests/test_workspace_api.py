import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_workspace_get_returns_current_tree(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "hello.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", app_root)
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    server.save_workspace_root(app_root)

    client = server.app.test_client()
    response = client.get("/api/workspace")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["path"] == str(app_root.resolve())
    assert any(child["name"] == "hello.txt" for child in payload["tree"]["children"])


def test_workspace_post_creates_and_switches_directory(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    workspace = tmp_path / "new-workspace"
    monkeypatch.setattr(server, "ROOT", app_root)
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")

    client = server.app.test_client()
    response = client.post("/api/workspace", json={"path": str(workspace)})

    assert response.status_code == 200
    assert workspace.exists()
    assert response.get_json()["path"] == str(workspace.resolve())


def test_workspace_get_follows_active_conversation_workspace(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    app_root.mkdir()
    first_workspace.mkdir()
    second_workspace.mkdir()
    (first_workspace / "first.txt").write_text("first", encoding="utf-8")
    (second_workspace / "second.txt").write_text("second", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", app_root)
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")

    server.save_workspace_root(first_workspace)
    server.append_session_turn("user", "first")
    first_id = server.load_session()["active_id"]
    server.create_conversation(workspace=str(second_workspace))
    server.append_session_turn("user", "second")
    server.activate_conversation(first_id)

    client = server.app.test_client()
    response = client.get("/api/workspace")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["path"] == str(first_workspace.resolve())
    assert any(child["name"] == "first.txt" for child in payload["tree"]["children"])
    assert not any(child["name"] == "second.txt" for child in payload["tree"]["children"])


def test_workspace_get_recovers_flattened_history_workspace(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    recovered_workspace = tmp_path / "old-project"
    outbox = recovered_workspace / ".agent" / "outbox"
    app_root.mkdir()
    outbox.mkdir(parents=True)
    (recovered_workspace / "old.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", app_root)
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")

    server.save_workspace_root(app_root)
    server.save_session({
        "mode": "dev_loop",
        "active_id": "old",
        "conversations": [
            {
                "id": "old",
                "title": "flattened",
                "workspace": str(app_root),
                "created_at": 1,
                "updated_at": 1,
                "turns": [
                    {
                        "role": "user",
                        "text": f"build report: {outbox / 'to_reasonix.md'}",
                        "ts": 1,
                        "messages": [],
                    }
                ],
                "events": [],
            }
        ],
    })

    client = server.app.test_client()
    response = client.get("/api/workspace")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["path"] == str(recovered_workspace.resolve())
    assert any(child["name"] == "old.txt" for child in payload["tree"]["children"])


def test_workspace_post_rejects_filesystem_root(monkeypatch, tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.setattr(server, "ROOT", app_root)
    root_path = Path(tmp_path.anchor)

    client = server.app.test_client()
    response = client.post("/api/workspace", json={"path": str(root_path)})

    assert response.status_code == 400
    assert "filesystem root" in response.get_json()["error"]


def test_validate_workspace_rejects_system_directory(monkeypatch, tmp_path):
    protected = tmp_path / "Windows"
    monkeypatch.setenv("SystemRoot", str(protected))

    try:
        server.validate_workspace_root(protected / "System32" / "project")
    except ValueError as exc:
        assert "protected system directory" in str(exc)
    else:
        raise AssertionError("expected protected system directory to be rejected")


def test_file_preview_uses_selected_workspace(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    workspace = tmp_path / "workspace"
    app_root.mkdir()
    workspace.mkdir()
    (workspace / "selected.md").write_text("# selected", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", app_root)
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    server.save_workspace_root(workspace)

    client = server.app.test_client()
    response = client.get("/api/file", query_string={"path": "selected.md"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["path"] == "selected.md"
    assert payload["content"] == "# selected"
