import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_history_api_returns_one_item_per_conversation(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})
    server.append_session_turn("user", "task one")
    server.append_session_event("plan", "plan one")
    server.create_conversation(workspace=str(tmp_path))
    server.append_session_turn("user", "task two")
    server.append_session_event("plan", "plan two")

    client = server.app.test_client()
    response = client.get("/api/history")

    assert response.status_code == 200
    conversations = response.get_json()["conversations"]
    assert [conversation["text"] for conversation in conversations] == ["task two", "task one"]
    assert [event["summary"] for event in conversations[0]["events"]] == ["plan two"]
    assert [event["summary"] for event in conversations[1]["events"]] == ["plan one"]


def test_history_api_includes_full_turn_messages(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})
    server.append_session_turn("user", "build a page")
    server.append_session_message("Codex", "ChatGPT plan", "full plan text")
    server.append_session_message("Reasonix", "DeepSeek build", "full build text")

    client = server.app.test_client()
    response = client.get("/api/history")

    assert response.status_code == 200
    conversation = response.get_json()["conversations"][0]
    assert conversation["message_count"] == 2
    assert [message["text"] for message in conversation["messages"]] == [
        "full plan text",
        "full build text",
    ]
    assert [message["text"] for message in conversation["turns"][0]["messages"]] == [
        "full plan text",
        "full build text",
    ]


def test_legacy_flat_session_migrates_turns_into_separate_conversations(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.SESSION_FILE.write_text(json.dumps({
        "mode": "dev_loop",
        "turns": [
            {"role": "user", "text": "旧对话一", "ts": 10, "messages": []},
            {"role": "user", "text": "旧对话二", "ts": 30, "messages": []},
        ],
        "events": [
            {"kind": "plan", "summary": "one event", "ts": 11},
            {"kind": "plan", "summary": "two event", "ts": 31},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    client = server.app.test_client()
    response = client.get("/api/history")

    assert response.status_code == 200
    conversations = response.get_json()["conversations"]
    assert [conversation["text"] for conversation in conversations] == ["旧对话二", "旧对话一"]
    assert [event["summary"] for event in conversations[0]["events"]] == ["two event"]
    assert [event["summary"] for event in conversations[1]["events"]] == ["one event"]


def test_previous_legacy_conversation_wrapper_is_split_back_out(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.SESSION_FILE.write_text(json.dumps({
        "mode": "dev_loop",
        "active_id": "legacy",
        "conversations": [{
            "id": "legacy",
            "title": "历史对话",
            "workspace": str(tmp_path),
            "created_at": 10,
            "updated_at": 30,
            "turns": [
                {"role": "user", "text": "误合并一", "ts": 10, "messages": []},
                {"role": "user", "text": "误合并二", "ts": 30, "messages": []},
            ],
            "events": [
                {"kind": "plan", "summary": "one event", "ts": 11},
                {"kind": "plan", "summary": "two event", "ts": 31},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    client = server.app.test_client()
    response = client.get("/api/history")

    assert response.status_code == 200
    conversations = response.get_json()["conversations"]
    assert [conversation["text"] for conversation in conversations] == ["误合并二", "误合并一"]
    assert [event["summary"] for event in conversations[0]["events"]] == ["two event"]
    assert [event["summary"] for event in conversations[1]["events"]] == ["one event"]


def test_migrated_history_ids_can_be_activated_after_reload(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.WORKSPACE_FILE = tmp_path / "workspace.json"
    server.SESSION_FILE.write_text(json.dumps({
        "mode": "dev_loop",
        "active_id": "legacy",
        "conversations": [{
            "id": "legacy",
            "title": "历史对话",
            "workspace": str(tmp_path),
            "created_at": 10,
            "updated_at": 30,
            "turns": [
                {"role": "user", "text": "旧一", "ts": 10, "messages": []},
                {"role": "user", "text": "旧二", "ts": 30, "messages": []},
            ],
            "events": [],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    client = server.app.test_client()
    history = client.get("/api/history").get_json()["conversations"]
    target_id = history[0]["id"]
    response = client.post(f"/api/history/{target_id}/activate")

    assert response.status_code == 200
    assert response.get_json()["conversation"]["id"] == target_id
