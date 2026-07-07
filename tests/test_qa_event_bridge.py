import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_qa_event_bridge_emits_external_event(monkeypatch):
    emitted = []
    server._qa_event_log.clear()
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    client = server.app.test_client()

    response = client.post("/api/qa-event", json={
        "kind": "reasonix_started",
        "label": "Reasonix",
        "title": "Reasonix worker started",
        "detail": "agent id 123",
        "conversation_id": "abc",
        "workspace": "H:/py/xiaocai",
    })

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert emitted[0][0] == "qa_external"
    assert emitted[0][1]["kind"] == "reasonix_started"
    assert emitted[0][1]["label"] == "Reasonix"
    assert emitted[0][1]["workspace"] == "H:/py/xiaocai"
    assert server._qa_event_log[-1]["kind"] == "reasonix_started"


def test_qa_event_bridge_requires_title_or_detail():
    client = server.app.test_client()

    response = client.post("/api/qa-event", json={"kind": "empty"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "qa_event_empty"


def test_qa_event_bridge_exposes_recent_events(monkeypatch):
    server._qa_event_log.clear()
    monkeypatch.setattr(server, "emit", lambda event, data: None)
    client = server.app.test_client()

    client.post("/api/qa-event", json={
        "kind": "packet_written",
        "label": "Codex",
        "title": "packet ready",
        "detail": "path: packet.md",
    })
    response = client.get("/api/qa-events")

    assert response.status_code == 200
    assert response.get_json()["events"][-1]["kind"] == "packet_written"


def test_qa_event_bridge_delete_clears_events_and_stops_watch(monkeypatch):
    server._qa_event_log.clear()
    server._qa_event_log.append({
        "kind": "reasonix_output",
        "label": "Reasonix",
        "title": "old",
        "detail": "old",
    })
    server._qa_watch_stop.clear()
    server._qa_watch_workspace = "H:/py/xiaocai"
    client = server.app.test_client()

    response = client.delete("/api/qa-events")

    assert response.status_code == 200
    assert response.get_json()["status"] == "cleared"
    assert server._qa_event_log == []
    assert server._qa_watch_stop.is_set()
