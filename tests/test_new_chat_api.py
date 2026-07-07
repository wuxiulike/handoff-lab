import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_new_chat_creates_empty_active_conversation(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})
    server.append_session_turn("user", "old task")
    old_id = server.load_session()["active_id"]

    client = server.app.test_client()
    response = client.post("/api/new-chat")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    session = server.load_session()
    assert session["active_id"] != old_id
    assert session["turns"] == []
    assert session["events"] == []
    assert len(session["conversations"]) == 2
