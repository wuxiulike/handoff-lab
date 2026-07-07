import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_worker_session_emits_lifecycle_events(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(server, "emit", lambda event_type, data: events.append((event_type, data)))

    session = server.WorkerSession("Reasonix", "build", tmp_path)
    session.start()
    session.progress_event("✓ edit_file app.py")
    session.complete("done")

    agent_events = [
        data for event_type, data in events
        if event_type == "agent_event"
    ]
    assert [event["kind"] for event in agent_events] == [
        "session_started",
        "progress",
        "session_completed",
    ]
    assert agent_events[0]["metadata"]["workspace"] == str(tmp_path)
    assert agent_events[2]["metadata"]["progress_count"] == 1

    token_events = [
        data for event_type, data in events
        if event_type == "token"
    ]
    assert token_events[0]["model"] == "Reasonix"
    assert "edit_file" in token_events[0]["text"]
