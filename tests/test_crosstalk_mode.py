import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_crosstalk_api_requires_cli_auth(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.AUTH_FILE = tmp_path / "auth.json"
    server.save_auth({"mode": "deny"})
    client = server.app.test_client()

    response = client.post("/api/crosstalk", json={"topic": "同学录"})

    assert response.status_code == 403
    assert response.get_json()["error"] == "authorization_required"


def test_real_dialogue_api_requires_cli_auth(tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.AUTH_FILE = tmp_path / "auth.json"
    server.save_auth({"mode": "deny"})
    client = server.app.test_client()

    response = client.post("/api/real-dialogue", json={"topic": "同学录"})

    assert response.status_code == 403
    assert response.get_json()["error"] == "authorization_required"


def test_real_dialogue_has_multiple_rounds():
    rounds = server.build_dialogue_round_prompts("世界杯又来了", rounds=3)

    assert len(rounds) == 3
    assert rounds[0]["round"] == 1
    assert rounds[-1]["round"] == 3


def test_real_dialogue_api_accepts_custom_round_count(monkeypatch, tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    server.AUTH_FILE = tmp_path / "auth.json"
    server.save_auth({"mode": "yolo"})
    captured = {}

    class FakeThread:
        def __init__(self, target, args, daemon):
            captured["args"] = args

        def start(self):
            pass

    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    monkeypatch.setattr(server, "emit", lambda event, data: None)
    client = server.app.test_client()

    response = client.post("/api/crosstalk", json={"topic": "世界杯", "rounds": 10})

    assert response.status_code == 200
    assert response.get_json()["rounds"] == 10
    assert captured["args"] == ("世界杯", 10)


def test_real_dialogue_calls_codex_each_round_with_transcript(monkeypatch, tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    seen_transcripts = []

    monkeypatch.setattr(server, "emit", lambda event, data: None)

    def fake_codex(topic, transcript, round_num, max_rounds, session=None):
        seen_transcripts.append([item.copy() for item in transcript])
        return f"codex-{round_num}"

    def fake_reasonix(topic, transcript, round_num, max_rounds):
        return f"reasonix-{round_num}"

    monkeypatch.setattr(server, "_run_codex_dialogue_turn", fake_codex)
    monkeypatch.setattr(server, "_run_reasonix_dialogue_turn", fake_reasonix)

    server._run_real_dialogue("topic")

    assert len(seen_transcripts) == 3
    assert seen_transcripts[0] == []
    assert seen_transcripts[1][-1] == {"speaker": "DeepSeek", "text": "reasonix-1"}
    assert seen_transcripts[2][-1] == {"speaker": "DeepSeek", "text": "reasonix-2"}


def test_real_dialogue_stops_before_next_model_call(monkeypatch, tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    emitted = []

    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "_run_codex_dialogue_turn", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("codex should not run")))

    server._stop_requested.set()
    try:
        server._run_real_dialogue("topic", rounds_count=3)
    finally:
        server._stop_requested.clear()

    assert ("stopped", {"reason": "user_graceful"}) in emitted
    assert any(event == "step_done" and data["step"] == "pipeline_complete" for event, data in emitted)


def test_real_dialogue_error_finishes_ui(monkeypatch, tmp_path):
    server.SESSION_FILE = tmp_path / "session.json"
    emitted = []

    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "_run_codex_dialogue_turn", lambda *args, **kwargs: "codex")
    monkeypatch.setattr(server, "_run_reasonix_dialogue_turn", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("DeepSeek 没有返回相声台词。")))

    server._run_real_dialogue("topic", rounds_count=1)

    assert any(event == "token" and "DeepSeek 没有返回" in data["text"] for event, data in emitted)
    assert any(
        event == "step_done" and data["step"] == "pipeline_complete" and "异常结束" in data["summary"]
        for event, data in emitted
    )


def test_codex_dialogue_turn_uses_app_worker(monkeypatch):
    class FakeWorker:
        def __init__(self):
            self.prompts = []

        def ask(self, prompt, on_token=None):
            self.prompts.append(prompt)
            on_token("hello")
            return "hello"

    emitted = []
    worker = FakeWorker()
    monkeypatch.setattr(server, "get_codex_app_worker", lambda: worker)
    monkeypatch.setattr(server, "emit_text_stream", lambda model, role, text, delay=0.0: emitted.append((model, role, text)))

    result = server._run_codex_dialogue_turn("topic", [], 1, 3)

    assert result == "hello"
    assert len(worker.prompts) == 1
    assert "topic" in worker.prompts[0]
    assert "emo" in worker.prompts[0]
    assert "颜文字" in worker.prompts[0]
    assert emitted == [("Codex", "ChatGPT", "hello")]


def test_stream_text_splits_into_characters(monkeypatch):
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))

    server.emit_text_stream("Codex", "ChatGPT", "你好")

    assert [data["text"] for event, data in emitted if event == "token"] == ["你", "好"]


def test_codex_dialogue_output_filters_hook_noise():
    lines = [
        "这世界杯一来，客厅就成了临时体育场。",
        "hook: Stop hook: Stop Failed",
        "tokens used: 123",
    ]

    assert server.filter_codex_dialogue_lines(lines) == ["这世界杯一来，客厅就成了临时体育场。"]


def test_streams_codex_clean_lines_immediately(monkeypatch):
    streamed = []
    monkeypatch.setattr(server, "emit_text_stream", lambda model, role, text, delay=0.0: streamed.append(text))

    captured = []
    for clean_line in server.filter_codex_dialogue_lines(["第一句", "hook: Stop hook: Stop Failed", "第二句"]):
        captured.append(clean_line)
        server.emit_text_stream("Codex", "ChatGPT", clean_line + "\n", delay=0.01)

    assert captured == ["第一句", "第二句"]
    assert streamed == ["第一句\n", "第二句\n"]
