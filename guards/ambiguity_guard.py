"""
Generic pre‑execution ambiguity guard.

Determines whether a task text is underspecified and should be blocked
with a NEED_CLARIFICATION / BLOCKED status instead of entering implementation
or generation paths.

Heuristics are language‑aware (zh‑CN / en) but DO NOT hard‑code any
specific company, document, person, or one‑off phrase.
"""

import re
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Generic signals – kept as sets so they are trivial to extend without
# introducing document‑specific knowledge.
# ---------------------------------------------------------------------------
ACTION_VERBS_ZH: set[str] = {
    "修复", "添加", "生成", "分析", "配置", "创建", "修改", "重构",
    "调整", "更新", "删除", "合并", "拆分", "优化", "编写", "实现",
    "支持", "替换", "插入", "导出", "导入", "转换", "校验",
}

ACTION_VERBS_EN: set[str] = {
    "fix", "add", "generate", "create", "modify", "update", "delete",
    "refactor", "analyze", "configure", "adjust", "optimize", "merge",
    "split", "implement", "support", "replace", "insert", "export",
    "import", "convert", "validate",
}

OBJECT_INDICATORS_ZH: set[str] = {
    "PPT", "模板", "路由", "文档", "测试", "文件", "代码", "页面",
    "布局", "溢出", "缺陷", "错误", "问题", "幻灯片", "目录",
}

OBJECT_INDICATORS_EN: set[str] = {
    "ppt", "template", "route", "document", "test", "file", "code",
    "page", "layout", "overflow", "defect", "bug", "error", "issue",
    "slide",
}

# Feasibility question patterns (generic, not tied to a specific phrase)
_FEASIBILITY_PATTERN = re.compile(
    r"能行吗|可以吗|能不能|可不可以|行不行|可行吗|是否可行|能不能行|"
    r"can (it|we|this)\b|is (it|this) (possible|feasible)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def is_underspecified(task: str) -> Tuple[bool, str]:
    """Return (True, reason_key) if the task text lacks enough information
    to be safely executed.  False, "" otherwise."""
    stripped = task.strip()

    # --- empty / whitespace ---
    if not stripped:
        return True, "task_is_empty"

    # --- very short text ---
    if len(stripped) < 5:
        # If it is *only* a bare feasibility question (no object / verb)
        if _FEASIBILITY_PATTERN.search(stripped) and not _has_object_or_verb(stripped):
            return True, "underspecified_feasibility_question"
        # Any other very‑short text with no discernible intent
        if not _has_object_or_verb(stripped):
            return True, "too_short_no_discernible_intent"

    # --- longer text that is still a feasibility question with no target ---
    if _FEASIBILITY_PATTERN.search(stripped):
        match = _FEASIBILITY_PATTERN.search(stripped)
        after_feasibility = stripped[match.end():].strip()
        # If nothing follows the question phrase (or only punctuation)
        if not after_feasibility or re.fullmatch(r"[?？\s]*", after_feasibility):
            return True, "underspecified_feasibility_question"
        # If the following part still lacks any object/verb indicator
        if not _has_object_or_verb(after_feasibility):
            return True, "underspecified_feasibility_question"

    # --- no action verb and the text is a question ---
    has_verb = any(v in stripped for v in (ACTION_VERBS_ZH | ACTION_VERBS_EN))
    if not has_verb:
        if stripped.endswith("?") or stripped.endswith("？"):
            if not _has_object_or_verb(stripped):
                return True, "no_action_verb_in_question"

    # Otherwise the task appears sufficiently specified
    return False, ""


def _has_object_or_verb(text: str) -> bool:
    """True when *text* contains at least one recognised object indicator
    or action verb."""
    return any(
        word in text
        for word in (OBJECT_INDICATORS_ZH | OBJECT_INDICATORS_EN | ACTION_VERBS_ZH | ACTION_VERBS_EN)
    )


# ---------------------------------------------------------------------------
# Pre‑execution guard for orchestrator integration
# ---------------------------------------------------------------------------

def pre_execution_guard(task_text: str) -> Optional[Dict]:
    """
    Check *task_text* before execution.

    Returns None if the task is clear enough to proceed.
    Otherwise returns a structured clarification response that the
    orchestrator can relay to the user immediately.
    """
    underspec, reason = is_underspecified(task_text)
    if not underspec:
        return None

    questions = _build_clarification_questions(task_text, reason)

    # Prefer NEED_CLARIFICATION if the orchestrator state machine supports it,
    # otherwise fall back to the generic BLOCKED status used by most pipelines.
    return {
        "status": "BLOCKED",             # change to "NEED_CLARIFICATION" if available
        "sub_status": "NEED_CLARIFICATION",
        "reason": reason,
        "message": "The task description is too vague to execute safely.",
        "questions": questions,
        "no_code_changes": True,
    }


def _build_clarification_questions(task_text: str, reason: str) -> list[str]:
    """Construct 1‑3 actionable questions to help the user clarify."""
    base = []
    if reason in {"task_is_empty", "too_short_no_discernible_intent"}:
        base.append("请提供需要执行的具体操作或目标。例如：'修复 PPT 内容溢出问题'。")
    elif "feasibility" in reason:
        base.append("请说明您想了解的可行性是针对哪一个功能、模块或目标。例如：'当前模板路由能否支持三层目录结构？'。")

    if not any("文件" in q or "模块" in q for q in base):
        base.append("请指出任务涉及的目标文件、功能或业务模块。")
    if len(base) < 3:
        base.append("请描述期望的结果或验收标准（例如：通过测试、生成文件、无溢出等）。")

    return base[:3]
