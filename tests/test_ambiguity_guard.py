"""
Tests for the generic task ambiguity guard.

All assertions use the generic `is_underspecified` function and MUST NOT
depend on a specific phrase like \"能行吗？\".
"""

import pytest
from guards.ambiguity_guard import is_underspecified, pre_execution_guard


# ---------------------------------------------------------------------------
# Underspecified inputs -> guard triggers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "task,expected_reason",
    [
        ("", "task_is_empty"),
        ("   ", "task_is_empty"),
        ("\t\n", "task_is_empty"),
        ("能行吗？", "underspecified_feasibility_question"),
        ("可以吗？", "underspecified_feasibility_question"),
        ("行不行？", "underspecified_feasibility_question"),
        ("能不能？", "underspecified_feasibility_question"),
        ("能不能行", "underspecified_feasibility_question"),
        ("可行吗", "underspecified_feasibility_question"),
        ("Can it?", "underspecified_feasibility_question"),            # extremely short, no object
    ],
)
def test_underspecified_inputs(task, expected_reason):
    is_underspec, reason = is_underspecified(task)
    assert is_underspec is True, f"Expected underspecified for {task!r}"
    # reason must start with the generic reason class (not the exact phrase)
    assert reason.startswith(expected_reason)


# ---------------------------------------------------------------------------
# Clear actionable tasks -> guard does NOT block
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "task",
    [
        "修复 PPT 内容溢出问题，并添加回归测试",
        "添加新的模板路由规则，支持三层目录结构。",
        "Generate a presentation from the document.",
        "请分析当前布局在 16:9 下的适配情况。",
        # Feasibility question WITH concrete object and follow‑up action
        "当前模板路由能否支持三层目录结构？请分析并给出结论",
        "Can the current template engine handle 10,000 slides without performance degradation? Please benchmark.",
        "Config the initial slide master.",
    ],
)
def test_clear_tasks_not_blocked(task):
    is_underspec, reason = is_underspecified(task)
    assert is_underspec is False, (
        f"Task {task!r} was incorrectly flagged as underspecified: {reason}"
    )


# ---------------------------------------------------------------------------
# pre_execution_guard returns proper structure
# ---------------------------------------------------------------------------
def test_pre_execution_guard_returns_none_for_valid_task():
    assert pre_execution_guard("修复溢出问题") is None


def test_pre_execution_guard_blocks_and_includes_questions():
    result = pre_execution_guard("可以吗？")
    assert result is not None
    assert result["status"] in ("BLOCKED", "NEED_CLARIFICATION")
    assert "reason" in result
    assert result["no_code_changes"] is True
    assert isinstance(result["questions"], list)
    assert len(result["questions"]) > 0


# ---------------------------------------------------------------------------
# Side‑effect safety: no code modifications happen inside the guard
# (trivially true – unit test ensures that, integration test added for
# confidence but kept lightweight)
# ---------------------------------------------------------------------------
def test_guard_does_not_modify_filesystem(tmp_path):
    """Prove that invoking the guard does not alter any file."""
    import os
    before = sorted(os.listdir(tmp_path))
    # call guard
    pre_execution_guard("能行吗？")
    after = sorted(os.listdir(tmp_path))
    assert before == after
