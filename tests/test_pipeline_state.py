"""Pipeline 显式状态机契约测试（server 层）。

固化 _run_pipeline 重构后的契约，防止未来回归：
- _classify_review_outcome：按 state.approved / state.status 分类
- 双重计数器 bug 回归：终止决策不依赖 round_num
- stage 函数停止/异常语义
- _finish_pipeline 事件契约（按 outcome 产出正确事件序列）

技术手段：monkeypatch codex_plan/reasonix_build/run_tests/codex_review/load_state/emit，
纯单元测试，不依赖真实模型。
"""
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


# ── _classify_review_outcome：状态分类契约 ────────────────────


def test_classify_approved():
    assert server._classify_review_outcome({"approved": True, "status": "APPROVED"}) == server.PIPELINE_APPROVED


def test_classify_max_round_reached():
    assert server._classify_review_outcome({"approved": False, "status": "MAX_ROUND_REACHED"}) == server.PIPELINE_MAX_ROUND


def test_classify_max_round_recommended_stop():
    assert server._classify_review_outcome({"approved": False, "status": "MAX_ROUND_RECOMMENDED_STOP"}) == server.PIPELINE_MAX_ROUND


def test_classify_changes_requested_continues():
    """CHANGES_REQUESTED 不在 state.status（ai_flow 把它翻译成 WAIT_REASONIX_FIX），
    且非 approved/max_round → 归为 RUNNING（继续下一轮）。"""
    assert server._classify_review_outcome({"approved": False, "status": "WAIT_REASONIX_FIX"}) == server.PIPELINE_RUNNING


def test_classify_wait_codex_review_continues():
    assert server._classify_review_outcome({"approved": False, "status": "WAIT_CODEX_REVIEW"}) == server.PIPELINE_RUNNING


def test_classify_unknown_continues():
    """未知 status 默认继续，不误终止。"""
    assert server._classify_review_outcome({"approved": False, "status": "UNKNOWN"}) == server.PIPELINE_RUNNING


# ── 双重计数器 bug 回归 ────────────────────────────────────


def test_termination_ignores_round_num(monkeypatch):
    """server 终止决策只依赖 state.approved/state.status，不读 round_num。

    这是双重计数器 bug 的回归保护：重构前 server 的 for 循环变量 round_num
    与 ai_flow 内部的 state['round'] 各自计数、可能漂移。重构后 round_num
    仅用于事件显示，不参与终止判断。
    """
    # 无论 state['round'] 是几，只要 approved=True 就终止为 APPROVED
    monkeypatch.setattr(server, "load_state", lambda: {"approved": True, "status": "APPROVED", "round": 99})
    assert server._classify_review_outcome() == server.PIPELINE_APPROVED

    # 无论 round 多大，只要非 approved/max_round 就继续
    monkeypatch.setattr(server, "load_state", lambda: {"approved": False, "status": "WAIT_REASONIX_FIX", "round": 999})
    assert server._classify_review_outcome() == server.PIPELINE_RUNNING


# ── stage 函数停止/异常语义 ────────────────────────────────


def _setup_isolated_session(monkeypatch, tmp_path):
    """隔离 session/workspace，避免污染真实 .agent。"""
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(server, "emit", lambda event, data: None)
    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: None)
    monkeypatch.setattr(server, "append_session_event", lambda *a, **k: None)
    monkeypatch.setattr(server, "append_session_message", lambda *a, **k: None)
    server._stop_requested.clear()


def test_stage_plan_returns_error_on_exception(monkeypatch, tmp_path):
    _setup_isolated_session(monkeypatch, tmp_path)
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))

    def boom(*a, **k):
        raise RuntimeError("plan failed")
    monkeypatch.setattr(server, "codex_plan", boom)
    monkeypatch.setattr(server, "make_recording_token_sink", lambda: (lambda t: None, []))
    monkeypatch.setattr(server, "save_captured_messages", lambda x: None)
    monkeypatch.setattr(server, "get_codex_app_worker", lambda: None)

    result = server._pipeline_stage_plan(1, 3)
    assert result == server.PIPELINE_ERROR
    # ❌ 标记在 role 字段（"❌错误"），text 是异常消息
    assert any(event == "token" and "❌" in data.get("role", "") for event, data in emitted)


def test_stage_plan_returns_stopped_when_requested(monkeypatch, tmp_path):
    _setup_isolated_session(monkeypatch, tmp_path)
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))

    monkeypatch.setattr(server, "codex_plan", lambda *a, **k: None)
    monkeypatch.setattr(server, "make_recording_token_sink", lambda: (lambda t: None, []))
    monkeypatch.setattr(server, "save_captured_messages", lambda x: None)
    monkeypatch.setattr(server, "get_codex_app_worker", lambda: None)

    server._stop_requested.set()
    try:
        result = server._pipeline_stage_plan(1, 3)
    finally:
        server._stop_requested.clear()

    assert result == server.PIPELINE_STOPPED
    assert ("stopped", {"reason": "user_graceful"}) in emitted


def test_stage_plan_normal_returns_none(monkeypatch, tmp_path):
    _setup_isolated_session(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "codex_plan", lambda *a, **k: None)
    monkeypatch.setattr(server, "make_recording_token_sink", lambda: (lambda t: None, []))
    monkeypatch.setattr(server, "save_captured_messages", lambda x: None)
    monkeypatch.setattr(server, "get_codex_app_worker", lambda: None)

    assert server._pipeline_stage_plan(1, 3) is None


def test_stage_test_swallows_exception(monkeypatch, tmp_path):
    """测试阶段异常被吞掉，始终返回 None（保持原行为）。"""
    _setup_isolated_session(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("test failed")
    monkeypatch.setattr(server, "run_tests", boom)

    assert server._pipeline_stage_test(1, 3) is None


def test_stage_review_approved(monkeypatch, tmp_path):
    _setup_isolated_session(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "codex_review", lambda *a, **k: None)
    monkeypatch.setattr(server, "make_recording_token_sink", lambda: (lambda t: None, []))
    monkeypatch.setattr(server, "save_captured_messages", lambda x: None)
    monkeypatch.setattr(server, "get_codex_app_worker", lambda: None)
    monkeypatch.setattr(server, "load_state", lambda: {"approved": True, "status": "APPROVED"})

    assert server._pipeline_stage_review(1, 3) == server.PIPELINE_APPROVED


def test_stage_review_changes_requested_continues(monkeypatch, tmp_path):
    _setup_isolated_session(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "codex_review", lambda *a, **k: None)
    monkeypatch.setattr(server, "make_recording_token_sink", lambda: (lambda t: None, []))
    monkeypatch.setattr(server, "save_captured_messages", lambda x: None)
    monkeypatch.setattr(server, "get_codex_app_worker", lambda: None)
    monkeypatch.setattr(server, "load_state", lambda: {"approved": False, "status": "WAIT_REASONIX_FIX"})

    assert server._pipeline_stage_review(1, 3) == server.PIPELINE_RUNNING


# ── _finish_pipeline 事件契约 ──────────────────────────────


def test_finish_approved_emits_correct_events(monkeypatch):
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: emitted.append(("agent_event", a)))

    server._finish_pipeline(server.PIPELINE_APPROVED)

    assert any(e == "agent_event" and a[2] == "Pipeline approved" for e, a in emitted)
    assert any(e == "step_done" and d.get("step") == "pipeline_complete" and "✅" in d.get("summary", "") for e, d in emitted)


def test_finish_max_round_emits_correct_events(monkeypatch):
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: emitted.append(("agent_event", a)))
    monkeypatch.setattr(server, "load_state", lambda: {"status": "MAX_ROUND_REACHED"})

    server._finish_pipeline(server.PIPELINE_MAX_ROUND)

    assert any(e == "agent_event" and a[2] == "MAX_ROUND_REACHED" for e, a in emitted)
    assert any(e == "step_done" and "⚠️" in d.get("summary", "") for e, d in emitted)


def test_finish_running_emits_all_rounds_completed(monkeypatch):
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: emitted.append(("agent_event", a)))

    server._finish_pipeline(server.PIPELINE_RUNNING)

    assert any(e == "agent_event" and a[2] == "All rounds completed" for e, a in emitted)


def test_finish_stopped_emits_no_completion(monkeypatch):
    """STOPPED 路径不发 pipeline_completed（保持原 _run_pipeline 直接 return 的行为）。"""
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: emitted.append(("agent_event", a)))

    server._finish_pipeline(server.PIPELINE_STOPPED)

    # 不应有 pipeline_completed 或 pipeline_complete
    assert not any(e == "agent_event" and "completed" in str(a[2]).lower() for e, a in emitted)
    assert not any(e == "step_done" and d.get("step") == "pipeline_complete" for e, d in emitted)


def test_finish_error_emits_no_completion(monkeypatch):
    """ERROR 路径同样不发完成事件。"""
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: emitted.append(("agent_event", a)))

    server._finish_pipeline(server.PIPELINE_ERROR)

    assert not any(e == "agent_event" and "completed" in str(a[2]).lower() for e, a in emitted)


# ── _run_pipeline 端到端契约（mock 所有 step）────────────────


def test_run_pipeline_stops_before_first_round(monkeypatch, tmp_path):
    """轮前检查：启动前 _stop_requested 已 set，立即停止、不发任何 stage 事件。"""
    _setup_isolated_session(monkeypatch, tmp_path)
    plan_calls = []
    monkeypatch.setattr(server, "codex_plan", lambda *a, **k: plan_calls.append(1))
    monkeypatch.setattr(server, "apply_model_config_env", lambda: None)
    monkeypatch.setattr(server, "apply_auth_env", lambda: None)
    monkeypatch.setattr(server, "load_workspace_root", lambda: tmp_path)

    server._stop_requested.set()
    try:
        server._run_pipeline(3)
    finally:
        server._stop_requested.clear()

    assert plan_calls == []  # 未进入任何 stage


def test_run_pipeline_completes_after_max_round_without_approval(monkeypatch, tmp_path):
    """所有轮次跑完仍未 approved → 正常结束（发 All rounds completed）。

    同时验证修复了死代码 bug：重构前每轮未终止都会误发 All rounds completed，
    重构后只在循环正常结束时发一次。
    """
    _setup_isolated_session(monkeypatch, tmp_path)
    completed_events = []
    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: completed_events.append(a))
    monkeypatch.setattr(server, "codex_plan", lambda *a, **k: None)
    monkeypatch.setattr(server, "reasonix_build", lambda *a, **k: None)
    monkeypatch.setattr(server, "run_tests", lambda *a, **k: None)
    monkeypatch.setattr(server, "codex_review", lambda *a, **k: None)
    monkeypatch.setattr(server, "summarize_reasonix_result", lambda: "summary")
    monkeypatch.setattr(server, "make_recording_token_sink", lambda: (lambda t: None, []))
    monkeypatch.setattr(server, "save_captured_messages", lambda x: None)
    monkeypatch.setattr(server, "get_codex_app_worker", lambda: None)
    monkeypatch.setattr(server, "load_state", lambda: {"approved": False, "status": "WAIT_REASONIX_FIX"})
    monkeypatch.setattr(server, "apply_model_config_env", lambda: None)
    monkeypatch.setattr(server, "apply_auth_env", lambda: None)
    monkeypatch.setattr(server, "load_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(server, "CODEX_FALLBACK_FAILURE_THRESHOLD", 99)
    # 屏蔽 WorkerSession 真实启动
    monkeypatch.setattr(server, "WorkerSession", lambda *a, **k: _FakeWorkerSession())

    server._run_pipeline(3)

    # 关键：All rounds completed 只发一次（死代码 bug 修复回归）
    all_done = [a for a in completed_events if a[2] == "All rounds completed"]
    assert len(all_done) == 1


def test_review_failure_signature_prefers_next_fix(monkeypatch):
    monkeypatch.setattr(
        server,
        "read_text",
        lambda path, default="": "  fix   the same issue\nagain  " if path == ".agent/next_fix.md" else "",
    )

    assert server._review_failure_signature({"status": "WAIT_REASONIX_FIX"}) == "fix the same issue again"


def test_run_pipeline_triggers_codex_fallback_after_three_same_reasonix_failures(monkeypatch, tmp_path):
    _setup_isolated_session(monkeypatch, tmp_path)
    emitted = []
    review_calls = {"count": 0}
    fallback_calls = []
    test_calls = []

    monkeypatch.setattr(server, "emit_agent_event", lambda *a, **k: emitted.append((a, k)))
    monkeypatch.setattr(server, "_pipeline_stage_plan", lambda round_num, max_round: None)
    monkeypatch.setattr(server, "_pipeline_stage_build", lambda round_num, max_round: None)
    monkeypatch.setattr(server, "_pipeline_stage_test", lambda round_num, max_round: test_calls.append(round_num) or None)

    def fake_review(round_num, max_round):
        review_calls["count"] += 1
        if review_calls["count"] <= 3:
            return server.PIPELINE_RUNNING
        return server.PIPELINE_APPROVED

    monkeypatch.setattr(server, "_pipeline_stage_review", fake_review)
    monkeypatch.setattr(server, "_review_failure_signature", lambda state=None: "same-review-finding")
    monkeypatch.setattr(server, "load_state", lambda: {"approved": False, "status": "WAIT_REASONIX_FIX"})

    def fake_fallback(round_num, max_round, same_failure_count):
        fallback_calls.append((round_num, max_round, same_failure_count))
        return None

    monkeypatch.setattr(server, "_pipeline_stage_codex_fallback_build", fake_fallback)
    monkeypatch.setattr(server, "apply_model_config_env", lambda: None)
    monkeypatch.setattr(server, "apply_auth_env", lambda: None)
    monkeypatch.setattr(server, "load_workspace_root", lambda: tmp_path)

    server._run_pipeline(3)

    assert fallback_calls == [(3, 3, 3)]
    assert review_calls["count"] == 4
    assert test_calls == [1, 2, 3, 3]
    assert any(a[0] == "codex_fallback_triggered" for a, _ in emitted)


class _FakeWorkerSession:
    def start(self): pass
    def progress_event(self, text): pass
    def complete(self, msg): pass
    def fail(self, exc): pass
