import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import ai_flow


class FakeWorker:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def ask(self, prompt, on_token=None):
        self.prompts.append(prompt)
        for token in ["A", "B"]:
            if on_token:
                on_token(token)
        return self.text


def test_codex_plan_stream_uses_app_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")

    ai_flow.ensure_dirs()
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    ai_flow.write_text(".agent/task.md", "build a thing")
    ai_flow.save_state({"round": 1, "max_round": 3, "status": "WAIT_CODEX_PLAN"})

    worker = FakeWorker("CODEX_PLAN_MD\nplan text\nACCEPTANCE_JSON\n{}")
    streamed = []

    ai_flow.codex_plan(on_token=lambda *args, **kwargs: streamed.append(args), worker=worker)

    assert worker.prompts
    assert streamed == [
        ("Codex", "ChatGPT 规划", "A", False),
        ("Codex", "ChatGPT 规划", "B", False),
    ]
    assert ai_flow.read_text(".agent/codex_plan_result.md") == worker.text
    assert ai_flow.read_text(".agent/codex_plan.md") == "plan text"


def test_codex_plan_skips_when_reasonix_fix_is_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")

    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/codex_plan.md", "keep this")
    ai_flow.save_state({"round": 2, "max_round": 3, "status": "WAIT_REASONIX_FIX"})
    worker = FakeWorker("new plan")
    streamed = []

    ai_flow.codex_plan(on_token=lambda *args, **kwargs: streamed.append(args), worker=worker)

    assert worker.prompts == []
    assert ai_flow.read_text(".agent/codex_plan.md") == "keep this"
    assert streamed == [
        ("System", "Pipeline", "跳过重新规划，Reasonix 将按 Codex 验收意见修复。\n", False),
    ]


def test_codex_review_stream_uses_app_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")

    ai_flow.ensure_dirs()
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    ai_flow.write_text(".agent/task.md", "build a thing")
    ai_flow.write_text(".agent/codex_plan.md", "plan")
    ai_flow.write_text(".agent/acceptance.json", "{}")
    ai_flow.write_text(".agent/reasonix_build.md", "build report")
    ai_flow.write_text(".agent/test.log", "tests ok")
    ai_flow.save_state({"round": 1, "max_round": 3, "status": "WAIT_CODEX_REVIEW"})

    review = {
        "status": "APPROVED",
        "risk_level": "low",
        "blocking_issues": [],
        "non_blocking_issues": [],
        "fix_instructions": [],
        "summary": "ok",
    }
    worker = FakeWorker(json.dumps(review, ensure_ascii=False))
    streamed = []

    ai_flow.codex_review(on_token=lambda *args, **kwargs: streamed.append(args), worker=worker)

    assert worker.prompts
    assert streamed == [
        ("Codex", "ChatGPT 验收", "A", False),
        ("Codex", "ChatGPT 验收", "B", False),
    ]
    assert json.loads(ai_flow.read_text(".agent/codex_review.json"))["status"] == "APPROVED"
    assert ai_flow.load_state()["status"] == "APPROVED"


def test_codex_review_gate_blocks_approval_without_real_test_log(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")

    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/task.md", "build a thing")
    ai_flow.write_text(".agent/codex_plan.md", "Tests to run: pytest")
    ai_flow.write_text(".agent/acceptance.json", "{}")
    ai_flow.write_text(".agent/build_report.md", "pytest passed 19/19")
    ai_flow.write_text(".agent/test.log", "No test command configured")
    ai_flow.save_state({"round": 1, "max_round": 3, "status": "WAIT_CODEX_REVIEW"})

    worker = FakeWorker(json.dumps({
        "status": "APPROVED",
        "risk_level": "low",
        "blocking_issues": [],
        "non_blocking_issues": [],
        "fix_instructions": [],
        "summary": "ok",
    }, ensure_ascii=False))

    ai_flow.codex_review(on_token=lambda *args, **kwargs: None, worker=worker)

    review = json.loads(ai_flow.read_text(".agent/codex_review.json"))
    assert review["status"] == "CHANGES_REQUESTED"
    assert review["blocking_issues"][0]["id"] == "EVIDENCE-TEST-LOG"
    assert ai_flow.load_state()["status"] == "WAIT_REASONIX_FIX"


def test_codex_review_gate_blocks_approval_without_visual_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")

    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/task.md", "把 H:\\a.docx 做成 PPT，并检查 PPT 是否美观")
    ai_flow.write_text(".agent/codex_plan.md", "需要 visual QA 和逐页渲染检查")
    ai_flow.write_text(".agent/acceptance.json", '{"visual_qa": true}')
    ai_flow.write_text(".agent/build_report.md", "generated H:\\a.pptx")
    ai_flow.write_text(".agent/test.log", "python -m pytest\n19 passed\nexit code 0")
    ai_flow.save_state({"round": 1, "max_round": 3, "status": "WAIT_CODEX_REVIEW"})

    worker = FakeWorker(json.dumps({
        "status": "APPROVED",
        "risk_level": "low",
        "blocking_issues": [],
        "non_blocking_issues": [],
        "fix_instructions": [],
        "summary": "ok",
    }, ensure_ascii=False))

    ai_flow.codex_review(on_token=lambda *args, **kwargs: None, worker=worker)

    review = json.loads(ai_flow.read_text(".agent/codex_review.json"))
    assert review["status"] == "CHANGES_REQUESTED"
    assert review["blocking_issues"][0]["id"] == "EVIDENCE-VISUAL"
    assert ai_flow.load_state()["status"] == "WAIT_REASONIX_FIX"


def test_visual_warning_gate_allows_resolved_primary_report():
    build_report = """
    ## artifacts_generated
    - `H:\\a.pptx`: Regenerated presentation, 23 slides, all checks passing - 0 warnings, 0 errors

    Fixture validation (all passed clean except pre-existing):
    | Fixture | Slides | Warnings | Notes |
    | H:\\a.docx | 23 | 0 | Primary acceptance target |
    | multi_heading.docx | 8 | 2 title_missing | Pre-existing fixture limitation |
    | with_table.docx | 4 | 1 font_inconsistency | Pre-existing fixture limitation |
    | long_text.docx | 6 | 0 | Fixed: was 3 slides + 1 text_density warning |

    ## known_risks
    - multi_heading.docx produces 2 pre-existing title_missing warnings; not affecting primary target.
    - Image extraction depends on python-docx relationship API.
    """

    assert not ai_flow.has_unresolved_visual_warning(build_report, "")


def test_visual_warning_gate_blocks_unresolved_warning():
    build_report = """
    ## known_risks
    - Slide 6 has overflow warning and no preview evidence.
    """

    assert ai_flow.has_unresolved_visual_warning(build_report, "")


def test_reasonix_build_stream_uses_reasonix_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    ai_flow.ensure_dirs()
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    ai_flow.write_text(".agent/task.md", "build a thing")
    ai_flow.write_text(".agent/codex_plan.md", "plan")
    ai_flow.write_text(".agent/acceptance.json", "{}")
    ai_flow.save_state({"round": 1, "max_round": 3, "status": "WAIT_REASONIX_BUILD"})

    calls = []

    def fake_cli(task, on_token=None, on_progress=None):
        calls.append(task)
        if on_token:
            on_token("X")
            on_token("Y")
        if on_progress:
            on_progress("✓ edit_file app.py")
        return "BUILD_REPORT_MD\nok"

    monkeypatch.setattr(ai_flow, "run_reasonix_cli", fake_cli)
    streamed = []

    ai_flow.reasonix_build(on_token=lambda *args, **kwargs: streamed.append((args, kwargs)))

    assert calls == [ai_flow.make_reasonix_cli_task()]
    assert streamed == [
        (("Reasonix", "DeepSeek Reasonix", "X"), {"is_thinking": False}),
        (("Reasonix", "DeepSeek Reasonix", "Y"), {"is_thinking": False}),
    ]
    assert ai_flow.read_text(".agent/logs/reasonix_stdout.log") == "BUILD_REPORT_MD\nok"
    assert ai_flow.load_state()["status"] == "WAIT_TEST"


def test_reasonix_fix_prompt_focuses_on_review_feedback(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")

    ai_flow.ensure_dirs()
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    ai_flow.write_text(".agent/task.md", "build a thing")
    ai_flow.write_text(".agent/codex_plan.md", "original plan")
    ai_flow.write_text(".agent/acceptance.json", "{}")
    ai_flow.write_text(".agent/next_fix.md", "fix only this")
    ai_flow.write_text(".agent/test.log", "tests failed")
    ai_flow.write_text(".agent/diff.patch", "diff --git a/x b/x")
    ai_flow.save_state({"round": 2, "max_round": 3, "status": "WAIT_REASONIX_FIX"})

    prompt = ai_flow.make_reasonix_prompt()

    assert "# Codex Review Fix Instructions" in prompt
    assert "fix only this" in prompt
    assert "Do not restate Codex's plan" in prompt
    assert "Do not rewrite the full previous answer" in prompt
    assert "tests failed" in prompt


def test_run_reasonix_cli_streams_subprocess_output(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow.shutil, "which", lambda name: "reasonix.cmd")
    monkeypatch.delenv("REASONIX_YOLO", raising=False)

    seen = {}

    class FakeStdout:
        def __init__(self, lines):
            self.lines = lines
            self.index = 0

        def readline(self):
            if self.index >= len(self.lines):
                return ""
            line = self.lines[self.index]
            self.index += 1
            return line

    class FakeProc:
        def __init__(self, args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            self.stdin = self
            self.writes = []
            self.stdout = FakeStdout([
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}}) + "\n",
                json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "s1",
                        "update": {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {"type": "text", "text": "想法"},
                        },
                    },
                }) + "\n",
                json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "s1",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "abc"},
                        },
                    },
                }) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}) + "\n",
            ])
            self.returncode = 0

        def write(self, text):
            self.writes.append(text)

        def flush(self):
            pass

        def close(self):
            pass

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(ai_flow.subprocess, "Popen", FakeProc)
    tokens = []

    def on_token(text, is_thinking=False):
        tokens.append((text, is_thinking))

    text = ai_flow.run_reasonix_cli("do it", on_token=on_token)

    assert text == "想法abc"
    assert tokens == [("想法", True), ("abc", False)]
    assert seen["args"] == [
        "reasonix.cmd",
        "acp",
        "--model",
        os.environ.get("REASONIX_MODEL", "deepseek-v4-pro"),
        "--dir",
        str(tmp_path),
    ]
    assert seen["kwargs"]["cwd"] == tmp_path
    assert seen["kwargs"]["stdin"] == ai_flow.subprocess.PIPE
    assert "简体中文" in seen["kwargs"]["env"]["REASONIX_ACP_SYSTEM_APPEND"]


def test_run_reasonix_cli_adds_yolo_only_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow.shutil, "which", lambda name: "reasonix.cmd")
    monkeypatch.setenv("REASONIX_YOLO", "1")
    seen = {}

    class FakeStdout:
        def __init__(self):
            self.lines = [
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}}) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}) + "\n",
            ]
            self.index = 0

        def readline(self):
            if self.index >= len(self.lines):
                return ""
            line = self.lines[self.index]
            self.index += 1
            return line

    class FakeProc:
        def __init__(self, args, **kwargs):
            seen["args"] = args
            self.stdin = self
            self.stdout = FakeStdout()
            self.returncode = 0

        def write(self, text): pass
        def flush(self): pass
        def close(self): pass
        def terminate(self): pass
        def wait(self, timeout=None): return self.returncode

    monkeypatch.setattr(ai_flow.subprocess, "Popen", FakeProc)

    ai_flow.run_reasonix_cli("do it")

    assert "--yolo" in seen["args"]


def test_run_reasonix_cli_emits_concise_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow.shutil, "which", lambda name: "reasonix.cmd")

    class FakeStdout:
        def __init__(self, lines):
            self.lines = lines
            self.index = 0

        def readline(self):
            if self.index >= len(self.lines):
                return ""
            line = self.lines[self.index]
            self.index += 1
            return line

    class FakeProc:
        def __init__(self, args, **kwargs):
            self.stdin = self
            self.stdout = FakeStdout([
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}}) + "\n",
                json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "Reading files now"}}},
                }) + "\n",
                json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"update": {"sessionUpdate": "tool_call", "title": "edit_file", "rawInput": {"path": "app.py"}}},
                }) + "\n",
                json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"update": {"sessionUpdate": "tool_call_update", "status": "completed", "content": [{"content": {"text": "line1\nline2"}}]}},
                }) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}) + "\n",
            ])
            self.returncode = 0

        def write(self, text):
            pass

        def flush(self):
            pass

        def close(self):
            pass

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(ai_flow.subprocess, "Popen", FakeProc)
    progress = []

    ai_flow.run_reasonix_cli("do it", on_progress=progress.append)

    assert "Reading files now" in progress
    assert "✓ edit_file  app.py" in progress
    assert "已完成 · 2 lines" in progress


def test_run_reasonix_cli_clips_tool_output_for_ui_but_keeps_full_log(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow.shutil, "which", lambda name: "reasonix.cmd")
    monkeypatch.setenv("REASONIX_UI_TOOL_MAX_CHARS", "10")
    long_output = "x" * 50

    class FakeStdout:
        def __init__(self, lines):
            self.lines = lines
            self.index = 0

        def readline(self):
            if self.index >= len(self.lines):
                return ""
            line = self.lines[self.index]
            self.index += 1
            return line

    class FakeProc:
        def __init__(self, args, **kwargs):
            self.stdin = self
            self.stdout = FakeStdout([
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}}) + "\n",
                json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "s1",
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "status": "completed",
                            "content": [{"content": {"type": "text", "text": long_output}}],
                        },
                    },
                }) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}) + "\n",
            ])
            self.returncode = 0

        def write(self, text):
            pass

        def flush(self):
            pass

        def close(self):
            pass

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(ai_flow.subprocess, "Popen", FakeProc)
    tokens = []

    text = ai_flow.run_reasonix_cli("do it", on_token=tokens.append)

    assert long_output in text
    assert any("已折叠" in token for token in tokens)
    assert not any(long_output in token for token in tokens)


def test_collect_workspace_diff_includes_claimed_untracked_files(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/build_report.md", "- `docx_to_ppt/cli.py`: implemented\n")
    (tmp_path / "docx_to_ppt").mkdir()
    (tmp_path / "docx_to_ppt" / "cli.py").write_text("print('pipeline')\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("print('old demo')\n", encoding="utf-8")

    def fake_capture(args):
        if args[:2] == ["git", "diff"]:
            return "tracked diff"
        if args[:3] == ["git", "ls-files", "--others"]:
            return "docx_to_ppt/cli.py\nunrelated.py\n"
        return ""

    monkeypatch.setattr(ai_flow, "run_capture", fake_capture)

    diff = ai_flow.collect_workspace_diff()

    assert "tracked diff" in diff
    assert "### docx_to_ppt/cli.py" in diff
    assert "print('pipeline')" in diff
    assert "### unrelated.py" not in diff
    assert "- unrelated.py" in diff


def test_collect_workspace_diff_includes_manifest_even_when_git_diff_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/build_report.md", "generated deck")
    (tmp_path / "out.pptx").write_bytes(b"pptx")
    (tmp_path / "report.json").write_text('{"ok": true}', encoding="utf-8")

    monkeypatch.setattr(ai_flow, "run_capture", lambda args: "")

    diff = ai_flow.collect_workspace_diff()

    assert "Workspace File Manifest" in diff
    assert "out.pptx" in diff
    assert "report.json" in diff
    assert "Git diff can be empty" in diff


def test_run_tests_preserves_reasonix_test_log_when_no_command(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/test.log", "python test_converter.py\n10 passed\nexit code 0\n")
    ai_flow.save_state({"round": 1, "max_round": 3, "status": "WAIT_TEST"})
    monkeypatch.setattr(ai_flow, "run_capture", lambda args: "")

    ai_flow.run_tests()

    assert "10 passed" in ai_flow.read_text(".agent/test.log")
    assert "No test command configured" not in ai_flow.read_text(".agent/test.log")
    assert ai_flow.load_state()["status"] == "WAIT_CODEX_REVIEW"


def test_collect_workspace_diff_embeds_referenced_report_contents(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()
    report = tmp_path / "a_ppt_check.json"
    qa = tmp_path / "a_ppt_qa.md"
    report.write_text('{"passed": true, "errors": [], "warnings": []}', encoding="utf-8")
    qa.write_text("# QA\nmax chars: 366\nmin font: 18pt\n", encoding="utf-8")
    ai_flow.write_text(".agent/build_report.md", f"Evidence: {report}\nEvidence: {qa}\n")
    monkeypatch.setattr(ai_flow, "run_capture", lambda args: "")

    diff = ai_flow.collect_workspace_diff()

    assert "Referenced artifact excerpts" in diff
    assert '"passed": true' in diff
    assert "max chars: 366" in diff


def test_make_codex_review_package_generates_missing_evidence_package(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/task.md", "写程序把H:\\a.doc做成ppt，要求美观大方")
    ai_flow.write_text(".agent/codex_plan.md", "plan")
    ai_flow.write_text(".agent/acceptance.json", "{}")
    ai_flow.write_text(".agent/build_report.md", "generated .agent/e2e_adoc.pptx")
    ai_flow.write_text(".agent/test.log", "python -m pytest\n71 passed\nexit code 0")
    (tmp_path / ".agent" / "e2e_adoc.pptx").write_bytes(b"pptx")
    monkeypatch.setattr(ai_flow, "run_capture", lambda args: "")

    prompt = ai_flow.make_codex_review_package()

    assert "Review Evidence Package" in prompt
    assert "Workspace File Manifest" in prompt
    assert "e2e_adoc.pptx" in prompt
    assert (tmp_path / ".agent" / "diff.patch").exists()


def test_make_codex_review_package_includes_codex_qa_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/task.md", "build a thing")
    ai_flow.write_text(".agent/codex_plan.md", "plan")
    ai_flow.write_text(".agent/acceptance.json", "{}")
    ai_flow.write_text(".agent/build_report.md", "READY_FOR_CODEX_REVIEW")
    ai_flow.write_text(".agent/test.log", "pytest\n1 passed\n")
    monkeypatch.setattr(ai_flow, "run_capture", lambda args: "")

    prompt = ai_flow.make_codex_review_package()

    assert "Codex QA Review Profile" in prompt
    assert "guidance_markdown" in prompt
    assert "Next Development Guidance" in prompt
    assert "Required Evidence For Next Round" in prompt


def test_review_schema_allows_guidance_and_structured_issues(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()

    ai_flow.ensure_review_schema()

    schema = json.loads((tmp_path / ".agent" / "schemas" / "review.schema.json").read_text(encoding="utf-8"))
    assert "guidance_markdown" in schema["properties"]
    assert schema["properties"]["blocking_issues"]["items"]["anyOf"][1]["type"] == "object"


def test_codex_review_writes_guidance_markdown_to_next_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_flow, "ROOT", tmp_path)
    monkeypatch.setattr(ai_flow, "AGENT", tmp_path / ".agent")
    monkeypatch.setattr(ai_flow, "OUTBOX", tmp_path / ".agent" / "outbox")
    monkeypatch.setattr(ai_flow, "LOGS", tmp_path / ".agent" / "logs")
    monkeypatch.setattr(ai_flow, "SCHEMAS", tmp_path / ".agent" / "schemas")
    monkeypatch.setattr(ai_flow, "STATE_FILE", tmp_path / ".agent" / "state.json")
    ai_flow.ensure_dirs()
    ai_flow.write_text(".agent/task.md", "build a thing")
    ai_flow.write_text(".agent/codex_plan.md", "plan")
    ai_flow.write_text(".agent/acceptance.json", "{}")
    ai_flow.write_text(".agent/build_report.md", "READY_FOR_CODEX_REVIEW")
    ai_flow.write_text(".agent/test.log", "pytest\n1 passed\n")
    ai_flow.save_state({"round": 1, "max_round": 3, "status": "WAIT_CODEX_REVIEW"})
    monkeypatch.setattr(ai_flow, "run_capture", lambda args: "")

    review = {
        "status": "CHANGES_REQUESTED",
        "risk_level": "medium",
        "blocking_issues": [{"id": "BI-001", "issue": "missing evidence", "evidence": "no log"}],
        "non_blocking_issues": [],
        "fix_instructions": ["write .agent/test.log"],
        "guidance_markdown": "## Conclusion\nNot approved.\n\n## Next Development Guidance\nAdd real test evidence.",
        "summary": "needs evidence",
    }
    worker = FakeWorker(json.dumps(review, ensure_ascii=False))

    ai_flow.codex_review(on_token=lambda *args, **kwargs: None, worker=worker)

    next_fix = ai_flow.read_text(".agent/next_fix.md")
    assert "# Codex QA Guidance" in next_fix
    assert "Add real test evidence." in next_fix
    assert "# Compact Fix Instructions" in next_fix
    assert "write .agent/test.log" in next_fix
