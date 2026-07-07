import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_session_defaults_when_no_file(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"

    session = server.load_session()

    assert session["turns"] == []
    assert session["mode"] == "dev_loop"


def test_append_session_turn_updates_context_metrics(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"

    session = server.append_session_turn("user", "帮我写一个web的同学录")
    metrics = server.context_metrics(session)

    assert session["turns"][0]["role"] == "user"
    assert metrics["turn_count"] == 1
    assert metrics["estimated_tokens"] > 0
    assert metrics["deepseek"]["limit"] == 1_000_000


def test_context_api_returns_metrics(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.append_session_turn("user", "hello")
    client = server.app.test_client()

    response = client.get("/api/context")

    assert response.status_code == 200
    assert response.get_json()["metrics"]["turn_count"] == 1
