import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.codex_app_worker import CODEX_APP_SERVER_COMMAND, CodexAppWorker, build_codex_app_server_command


class FakeTransport:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    def write(self, payload):
        self.sent.append(payload)

    def read(self):
        if not self.messages:
            raise EOFError("no more messages")
        return self.messages.pop(0)


def test_worker_initializes_and_sends_initialized_notification():
    transport = FakeTransport([
        {"id": "1", "result": {"protocolVersion": "1", "capabilities": {}}},
    ])
    worker = CodexAppWorker(transport=transport)

    worker.initialize()

    assert transport.sent[0]["method"] == "initialize"
    assert transport.sent[0]["params"]["capabilities"]["experimentalApi"] is True
    assert transport.sent[1]["method"] == "initialized"


def test_default_command_uses_http_only_openai_provider():
    assert 'model_provider="openai-no-ws"' in CODEX_APP_SERVER_COMMAND
    assert "supports_websockets=false" in CODEX_APP_SERVER_COMMAND


def test_command_honors_codex_cli_env(monkeypatch):
    monkeypatch.setenv("CODEX_CLI", "/opt/bin/codex")

    assert build_codex_app_server_command().startswith('"/opt/bin/codex" app-server')


def test_worker_reuses_thread_for_follow_up_turns():
    transport = FakeTransport([
        {"id": "1", "result": {"protocolVersion": "1", "capabilities": {}}},
        {"id": "2", "result": {"thread": {"id": "thread-1"}}},
        {"id": "3", "result": {"turn": {"id": "turn-1"}}},
        {"method": "item/agentMessage/delta", "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "first"}},
        {"method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}}},
        {"id": "4", "result": {"turn": {"id": "turn-2"}}},
        {"method": "item/agentMessage/delta", "params": {"threadId": "thread-1", "turnId": "turn-2", "delta": "second"}},
        {"method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-2", "status": "completed"}}},
    ])
    worker = CodexAppWorker(transport=transport)
    tokens = []

    assert worker.ask("hello", on_token=tokens.append) == "first"
    assert worker.ask("again", on_token=tokens.append) == "second"

    turn_starts = [message for message in transport.sent if message.get("method") == "turn/start"]
    assert len(turn_starts) == 2
    assert turn_starts[0]["params"]["threadId"] == "thread-1"
    assert turn_starts[1]["params"]["threadId"] == "thread-1"
    assert tokens == ["first", "second"]
