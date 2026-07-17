#!/usr/bin/env python3
"""ai_flow.py — Codex + Reasonix 双智能体自动开发流水线编排器 v0.1

Codex = 架构师 + 审稿人 + 质检员
Reasonix = 代码施工员
ai_flow.py = 项目经理
Git = 版本记录员
pytest/npm test = 自动验收机
用户 = 最终负责人

Usage:
  python tools/ai_flow.py init "<task description>"
  python tools/ai_flow.py codex-plan
  python tools/ai_flow.py reasonix-build
  python tools/ai_flow.py test --test-cmd "pytest"
  python tools/ai_flow.py codex-review
  python tools/ai_flow.py one-round --test-cmd "pytest"
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tools.codex_qa_profile import (
    format_next_fix_guidance,
    review_profile_prompt,
    review_schema_extension,
)


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / ".agent"
OUTBOX = AGENT / "outbox"
LOGS = AGENT / "logs"
SCHEMAS = AGENT / "schemas"

STATE_FILE = AGENT / "state.json"


# Codex 技能隔离环境
_CODEX_ENV = None
_CODEX_APP_WORKER = None


def set_workspace_root(path):
    global ROOT, AGENT, OUTBOX, LOGS, SCHEMAS, STATE_FILE, _CODEX_ENV, _CODEX_APP_WORKER
    ROOT = Path(path).expanduser().resolve()
    AGENT = ROOT / ".agent"
    OUTBOX = AGENT / "outbox"
    LOGS = AGENT / "logs"
    SCHEMAS = AGENT / "schemas"
    STATE_FILE = AGENT / "state.json"
    _CODEX_ENV = None
    if _CODEX_APP_WORKER is not None:
        _CODEX_APP_WORKER.close()
        _CODEX_APP_WORKER = None
    return ROOT


def _get_codex_env():
    global _CODEX_ENV
    if _CODEX_ENV is None:
        empty_dir = AGENT / "empty_skills"
        empty_dir.mkdir(parents=True, exist_ok=True)
        _CODEX_ENV = os.environ.copy()
        _CODEX_ENV["CODEX_SKILLS_DIR"] = str(empty_dir)
    return _CODEX_ENV


def get_codex_app_worker():
    global _CODEX_APP_WORKER
    if _CODEX_APP_WORKER is None:
        from tools.codex_app_worker import CodexAppWorker

        _CODEX_APP_WORKER = CodexAppWorker(cwd=ROOT, env=_get_codex_env())
    return _CODEX_APP_WORKER


def run_codex_app_worker(prompt: str, role: str, output_path: str, on_token=None, worker=None) -> str:
    worker = worker or get_codex_app_worker()

    def handle_token(token: str):
        if on_token:
            on_token("Codex", role, token, False)

    text = worker.ask(prompt, on_token=handle_token).strip()
    write_text(output_path, text)
    return text


def should_surface_codex_status(line: str) -> bool:
    """Return True for Codex CLI connection status lines worth showing in UI."""
    lower = line.lower()
    return (
        "reconnecting" in lower
        or "request timed out" in lower
        or "falling back" in lower
    )


def split_codex_status_lines(line: str) -> list[str]:
    parts = re.findall(
        r"(?:ERROR:\s*)?Reconnecting\.\.\.\s*\d+/\d+|"
        r"warning:\s*Falling back[^.]*\.|"
        r"request timed out",
        line,
        flags=re.IGNORECASE,
    )
    return [part.strip() for part in parts] or [line.strip()]


def ensure_dirs():
    for p in [AGENT, OUTBOX, LOGS, SCHEMAS]:
        p.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd, log_path=None, input_text=None, check=False, env=None):
    print(f"\n$ {cmd}\n")
    kwargs = dict(cwd=ROOT, shell=True, input=input_text, text=True, encoding="utf-8",
                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if env:
        kwargs["env"] = env
    result = subprocess.run(cmd, **kwargs)

    if log_path:
        Path(log_path).write_text(result.stdout, encoding="utf-8", errors="ignore")

    if result.returncode != 0:
        print(result.stdout)
        if check:
            raise RuntimeError(f"Command failed: {cmd}")

    return result


def read_text(path, default=""):
    path = ROOT / path if not Path(path).is_absolute() else Path(path)
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path, content):
    path = ROOT / path if not Path(path).is_absolute() else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_state():
    if not STATE_FILE.exists():
        return {
            "task_id": "manual-task",
            "round": 1,
            "max_round": 3,
            "status": "INIT",
            "approved": False
        }
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Step 0: init
# ---------------------------------------------------------------------------

def init_task(task_text, task_id="manual-task", max_round=3):
    """初始化任务：写入 task.md + state.json"""
    ensure_dirs()

    write_text(".agent/task.md", task_text)

    state = {
        "task_id": task_id,
        "round": 1,
        "max_round": max_round,
        "status": "WAIT_CODEX_PLAN",
        "approved": False,
        "last_actor": "user",
        "next_actor": "codex"
    }

    save_state(state)

    print("Task initialized:")
    print(f"- task_id: {task_id}")
    print(f"- file: .agent/task.md")


# ---------------------------------------------------------------------------
# Step 1: Codex 规划
# ---------------------------------------------------------------------------

def make_codex_plan_prompt():
    """生成 Codex 规划提示词 → .agent/outbox/to_codex_plan.md"""
    task = read_text(".agent/task.md")
    agents = read_text("AGENTS.md")

    prompt = f"""
# Role

You are the architect and acceptance designer.

You must create an implementation plan for Reasonix. Do not modify code in this step.
Do NOT invoke any skills. Do NOT read skill files. Do NOT use writing-plans or using-superpowers.
Output the plan directly without running any tools.

# Original Task

{task}

# Project Agent Rules

{agents}

# Output Requirements

Create a detailed implementation plan and acceptance criteria.

Return markdown with two sections:

1. CODEX_PLAN_MD
2. ACCEPTANCE_JSON

The ACCEPTANCE_JSON must be valid JSON.

# Plan Requirements

The plan must include:

- task understanding
- affected modules
- implementation steps
- tests to run
- new tests to add
- forbidden shortcuts
- acceptance criteria
- rollback notes

# Important

For this project, generic implementation is more important than passing one sample document.
"""
    write_text(".agent/outbox/to_codex_plan.md", prompt)
    return prompt


def codex_plan(on_token=None, worker=None):
    """执行 Codex 规划，解析结果写入 codex_plan.md 和 acceptance.json"""
    ensure_dirs()
    state = load_state()
    if state.get("status") == "WAIT_REASONIX_FIX":
        if on_token:
            on_token("System", "Pipeline", "跳过重新规划，Reasonix 将按 Codex 验收意见修复。\n", False)
        return

    prompt = make_codex_plan_prompt()

    if on_token:
        run_codex_app_worker(
            prompt,
            "ChatGPT 规划",
            ".agent/codex_plan_result.md",
            on_token=on_token,
            worker=worker,
        )
    elif False and on_token:
        # 流式模式：Popen 实时读取 stdout
        proc = subprocess.Popen(
            "codex exec --ignore-rules --ephemeral -s danger-full-access - -o .agent/codex_plan_result.md",
            cwd=ROOT, shell=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", env=_get_codex_env()
        )
        proc.stdin.write(prompt)
        proc.stdin.close()

        in_response = False  # 等待模型回复开始
        for line in iter(proc.stdout.readline, ""):
            stripped = line.strip()
            if not stripped:
                continue
            # 跳过连接/重连/错误行
            if should_surface_codex_status(stripped):
                for status_line in split_codex_status_lines(stripped):
                    on_token("System", "Codex连接", status_line + "\n", is_thinking=False)
                continue
            if "ERROR:" in stripped or "warning:" in stripped:
                continue
            # 拦截技能文件读取
            if ".agents\\skills\\" in stripped or "Get-Content -Raw" in stripped or "powershell.exe" in stripped:
                continue
            if stripped.startswith("succeeded in") or stripped.startswith("name: ") or stripped.startswith("description:"):
                continue
            # "codex" 独立行 = 模型回复开始标志
            if stripped == "codex" and not in_response:
                in_response = True
                on_token("Codex", "🧠规划", "", is_thinking=True)
                continue
            if not in_response:
                continue
            # "tokens used" = 结束
            if "tokens used" in stripped:
                break
            # exec / shell 命令回显跳过
            if stripped.startswith("exec") or stripped.startswith("$"):
                continue
            # 再次拦截技能关键词
            if "using-superpowers" in stripped or "writing-plans" in stripped:
                continue
            on_token("Codex", "🧠规划", stripped + "\n", is_thinking=False)
        proc.wait()
    else:
        result = run_cmd(
            "codex exec --ignore-rules --ephemeral -s danger-full-access - -o .agent/codex_plan_result.md",
            log_path=LOGS / "codex_plan.log",
            input_text=prompt, env=_get_codex_env()
        )

    plan_result = read_text(".agent/codex_plan_result.md")

    # 尝试拆分 plan 和 acceptance JSON
    # Codex 输出格式：## CODEX_PLAN_MD ... ## ACCEPTANCE_JSON ...
    plan_text = plan_result
    acceptance_json = "{}"

    if "ACCEPTANCE_JSON" in plan_result:
        parts = plan_result.split("ACCEPTANCE_JSON", 1)
        plan_text = parts[0].replace("CODEX_PLAN_MD", "").strip()
        json_part = parts[1].strip()

        # 提取 JSON 块
        if "```json" in json_part:
            json_part = json_part.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in json_part:
            json_part = json_part.split("```", 1)[1].split("```", 1)[0]
        elif "{" in json_part:
            json_part = json_part[json_part.index("{"):]
            if "}" in json_part:
                json_part = json_part[:json_part.rindex("}") + 1]

        try:
            json.loads(json_part)
            acceptance_json = json_part
        except json.JSONDecodeError:
            print("Warning: Could not parse ACCEPTANCE_JSON, using raw output")

    write_text(".agent/codex_plan.md", plan_text)
    write_text(".agent/acceptance.json", acceptance_json)

    state = load_state()
    state["status"] = "WAIT_REASONIX_BUILD"
    state["last_actor"] = "codex"
    state["next_actor"] = "reasonix"
    save_state(state)

    print("Codex plan generated:")
    print("- .agent/codex_plan.md")
    print("- .agent/acceptance.json")


# ---------------------------------------------------------------------------
# Step 2: Reasonix 施工
# ---------------------------------------------------------------------------

def make_reasonix_prompt():
    """生成 Reasonix 施工提示词 → .agent/outbox/to_reasonix.md"""
    task = read_text(".agent/task.md")
    plan = read_text(".agent/codex_plan.md")
    acceptance = read_text(".agent/acceptance.json")
    agents = read_text("AGENTS.md")
    reasonix_memory = read_text("REASONIX.md")
    state = load_state()
    is_fix_round = state.get("status") == "WAIT_REASONIX_FIX" or state.get("round", 1) > 1
    next_fix = read_text(".agent/next_fix.md")
    test_log = read_text(".agent/test.log")
    diff = read_text(".agent/diff.patch")

    if is_fix_round:
        prompt = f"""
# Role

You are the implementation engineer for a fix round.

Do not restate Codex's plan. Do not rewrite the full previous answer.
Only inspect the repository, apply the requested fixes, run relevant tests, and write `.agent/build_report.md`.

# Original Task

{task}

# Codex Review Fix Instructions

{next_fix or "Codex requested changes but did not provide detailed fix instructions. Inspect the review context, tests, and diff, then make the smallest safe fix."}

# Acceptance Criteria

{acceptance}

# Previous Plan Reference

Use this only as background. Do not copy it back into your response.

{plan}

# Current Test Log

{test_log}

# Current Git Diff

```diff
{diff}
```

# AGENTS.md

{agents}

# REASONIX.md

{reasonix_memory}

# Hard Rules

1. Fix only the issues requested by Codex review.
2. Do not echo the plan, review, diff, or this prompt back to the user.
3. Do not hardcode document-specific content.
4. Do not delete or weaken tests.
5. Do not commit, push, deploy, or modify files outside this repository.
6. Do not print secrets.
7. After implementation, write `.agent/build_report.md`.

# Build Report Format

Write `.agent/build_report.md` with this structure:

## files_changed

- path/to/file: concise reason, one line each

## artifacts_generated

- path/to/output: what it proves or contains

## implementation_summary

Short bullet summary only. Do not paste code, command transcripts, or long logs.

## tests_run

List commands executed and pass/fail result. Keep raw logs in `.agent/test.log` or tool logs.

## acceptance_evidence

List report/preview/output paths Codex should inspect, especially PNG/PDF/PPTX/JSON/MD evidence.

## known_risks

List risks or write "none".

## unresolved_questions

List questions or write "none".

# Completion

When finished, stop. Do not ask for Codex review yourself. Keep the response concise; Codex can inspect files directly.
"""
        write_text(".agent/outbox/to_reasonix.md", prompt)
        return prompt

    prompt = f"""
# Role

You are the implementation engineer.

You must follow Codex's plan exactly. You are not the architect and you are not the final reviewer.
Do not restate Codex's plan. Do not rewrite the full previous answer.
Only inspect the repository, implement the plan, run relevant tests, and write `.agent/build_report.md`.

# Original Task

{task}

# Codex Plan

{plan}

# Acceptance Criteria

{acceptance}

# AGENTS.md

{agents}

# REASONIX.md

{reasonix_memory}

# Hard Rules

1. Only modify files directly related to this task.
2. Do not echo the plan, acceptance criteria, or this prompt back to the user.
3. Do not hardcode document-specific content.
4. Do not add routing branches based on a specific company name, document title, person name, or one-off phrase.
5. Do not delete or weaken tests.
6. Do not change public APIs unless Codex explicitly requires it.
7. Do not commit, push, deploy, or modify files outside this repository.
8. Do not print secrets.
9. After implementation, write `.agent/build_report.md`.

# Build Report Format

Write `.agent/build_report.md` with this structure:

## files_changed

- path/to/file: concise reason, one line each

## artifacts_generated

- path/to/output: what it proves or contains

## implementation_summary

Short bullet summary only. Do not paste code, command transcripts, or long logs.

## tests_run

List commands executed and pass/fail result. Keep raw logs in `.agent/test.log` or tool logs.

## acceptance_evidence

List report/preview/output paths Codex should inspect, especially PNG/PDF/PPTX/JSON/MD evidence.

## known_risks

List risks or write "none".

## unresolved_questions

List questions or write "none".

# Completion

When finished, stop. Do not ask for Codex review yourself. Keep the response concise; Codex can inspect files directly.
"""
    write_text(".agent/outbox/to_reasonix.md", prompt)
    return prompt


def make_reasonix_cli_task() -> str:
    prompt_path = (ROOT / ".agent" / "outbox" / "to_reasonix.md").resolve()
    return (
        f"Read the UTF-8 construction order at {prompt_path}. "
        "Follow it exactly, use filesystem and shell tools as needed, then write .agent/build_report.md. "
        "Do not merely summarize or echo the order. Implement the requested changes."
    )


def resolve_reasonix_cli() -> str:
    configured = os.environ.get("REASONIX_CLI")
    if configured:
        return configured

    for name in ("reasonix.cmd", "reasonix.exe", "reasonix"):
        found = shutil.which(name)
        if found:
            return found

    raise RuntimeError("未找到 reasonix CLI，请先安装：npm install -g reasonix")


def run_reasonix_cli(task: str, on_token=None, on_progress=None) -> str:
    cli = resolve_reasonix_cli()
    model = os.environ.get("REASONIX_MODEL", "deepseek-v4-pro")
    cmd = [cli, "acp", "--model", model]
    max_ui_chars = int(os.environ.get("REASONIX_UI_MAX_CHARS", "1200"))
    max_tool_chars = int(os.environ.get("REASONIX_UI_TOOL_MAX_CHARS", "800"))
    env = os.environ.copy()
    system_append = env.get("REASONIX_ACP_SYSTEM_APPEND", "")
    chinese_output_rule = (
        "请始终使用简体中文输出，包括思考摘要、工具说明、执行进度、错误解释和最终总结。"
        "不要用英文复述计划；不要输出大段未折叠日志。"
    )
    env["REASONIX_ACP_SYSTEM_APPEND"] = (
        f"{system_append}\n\n{chinese_output_rule}" if system_append else chinese_output_rule
    )

    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1,
    )

    full_chunks = []
    ui_chunks = []
    next_id = 1
    assert proc.stdout is not None
    assert proc.stdin is not None

    def append_full(text: str):
        if not text:
            return
        full_chunks.append(text)

    def clip_for_ui(text: str, limit: int = max_ui_chars) -> str:
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return f"{text[:limit]}\n\n[已折叠 {omitted} 个字符，完整内容见 .agent/logs/reasonix_stdout.log]\n"

    def emit_text(text: str, limit: int = max_ui_chars, is_thinking: bool = False):
        if not text:
            return
        ui_text = clip_for_ui(text, limit)
        ui_chunks.append(ui_text)
        if on_token:
            try:
                on_token(ui_text, is_thinking)
            except TypeError:
                on_token(ui_text)

    def emit_progress(text: str):
        if not text or not on_progress:
            return
        on_progress(text.strip())

    def summarize_tool_call(title: str, raw_input) -> str:
        title = title or "tool"
        target = ""
        if isinstance(raw_input, dict):
            for key in (
                "path", "file", "file_path", "filepath", "command", "cmd",
                "query", "pattern", "glob", "text",
            ):
                value = raw_input.get(key)
                if isinstance(value, str) and value.strip():
                    target = value.strip()
                    break
        elif isinstance(raw_input, str):
            target = raw_input.strip()
        if len(target) > 120:
            target = target[:117] + "..."
        return f"✓ {title}" + (f"  {target}" if target else "")

    def summarize_tool_update(update: dict) -> str:
        status = update.get("status")
        content = update.get("content") or []
        line_count = 0
        for item in content:
            nested = item.get("content") if isinstance(item, dict) else None
            if isinstance(nested, dict):
                line_count += len((nested.get("text") or "").splitlines())
        if status == "completed":
            return f"  已完成" + (f" · {line_count} lines" if line_count else "")
        if status:
            return f"  {status}"
        return ""

    def summarize_agent_message(text: str) -> str:
        text = " ".join((text or "").split())
        if not text:
            return ""
        if len(text) > 260:
            text = text[:257] + "..."
        return text

    def record_and_emit(text: str, limit: int = max_ui_chars, is_thinking: bool = False):
        append_full(text)
        emit_text(text, limit, is_thinking=is_thinking)

    def write_message(message: dict):
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def handle_notification(message: dict):
        method = message.get("method")
        params = message.get("params") or {}
        if method == "session/update":
            update = params.get("update") or {}
            kind = update.get("sessionUpdate")
            if kind in ("agent_message_chunk", "agent_thought_chunk"):
                content = update.get("content") or {}
                text = content.get("text", "")
                append_full(text)
                if kind == "agent_message_chunk":
                    progress = summarize_agent_message(text)
                    if progress and on_progress:
                        emit_progress(progress)
                    else:
                        emit_text(text, is_thinking=False)
                else:
                    emit_text(text, is_thinking=True)
            elif kind == "tool_call":
                title = update.get("title") or "tool"
                raw_input = update.get("rawInput")
                emit_progress(summarize_tool_call(title, raw_input))
                if raw_input is not None:
                    detail = json.dumps(raw_input, ensure_ascii=False)
                    append_full(f"\n[tool] {title}: {detail}\n")
                    emit_text(f"\n[tool] {title}: {clip_for_ui(detail, 240)}\n", 360)
                else:
                    record_and_emit(f"\n[tool] {title}\n", 120)
            elif kind == "tool_call_update":
                status = update.get("status")
                content = update.get("content") or []
                emit_progress(summarize_tool_update(update))
                if status:
                    record_and_emit(f"\n[tool:{status}]\n", 80)
                for item in content:
                    nested = item.get("content") if isinstance(item, dict) else None
                    if isinstance(nested, dict):
                        tool_text = nested.get("text", "")
                        append_full(tool_text)
                        emit_text(tool_text, max_tool_chars)
        elif method == "session/request_permission":
            write_message({
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {"outcome": {"outcome": "selected", "optionId": "allow_once"}},
            })

    def request(method: str, params: dict) -> dict:
        nonlocal next_id
        request_id = next_id
        next_id += 1
        write_message({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            line = proc.stdout.readline()
            if line == "":
                raise RuntimeError(f"Reasonix ACP exited before {method} completed")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                record_and_emit(line)
                continue
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", f"{method} failed"))
                return message.get("result") or {}
            if "method" in message:
                handle_notification(message)

    completed = False
    try:
        request("initialize", {})
        session = request("session/new", {"cwd": str(ROOT)})
        session_id = session["sessionId"]
        result = request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": task}]},
        )
        stop_reason = result.get("stopReason")
        if stop_reason not in (None, "end_turn"):
            record_and_emit(f"\n[Reasonix stopReason: {stop_reason}]\n")
        completed = True
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass

    result_text = "".join(full_chunks)
    try:
        returncode = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait()
    if returncode != 0 and not completed:
        raise RuntimeError(f"Reasonix CLI failed with exit code {returncode}\n{result_text}")
    return result_text


def reasonix_build(on_token=None, on_progress=None):
    """执行 Reasonix 施工 —— 调用 Reasonix CLI，让 worker 可读写文件并运行命令。"""
    ensure_dirs()
    make_reasonix_prompt()
    task = make_reasonix_cli_task()

    if not on_token:
        print("Calling Reasonix CLI...")

    result_text = run_reasonix_cli(
        task,
        on_progress=on_progress,
        on_token=(
            lambda token, is_thinking=False: on_token(
                "Reasonix", "DeepSeek Reasonix", token, is_thinking=is_thinking
            )
        ) if on_token else None,
    )

    # 保存原始输出
    write_text(".agent/logs/reasonix_stdout.log", result_text)

    # 解析 file:path 格式的代码块，写入对应文件
    import re
    pattern = re.compile(r"```file:(.+?)\n(.*?)```", re.DOTALL)
    files_written = []
    for match in pattern.finditer(result_text):
        filepath = match.group(1).strip()
        content = match.group(2)
        write_text(filepath, content)
        files_written.append(filepath)
        print(f"  wrote: {filepath}")

    if not files_written:
        print("  (no file blocks found in response)")

    state = load_state()
    state["status"] = "WAIT_TEST"
    state["last_actor"] = "reasonix"
    state["next_actor"] = "orchestrator"
    save_state(state)

    print("Reasonix build finished:")
    print(f"- .agent/logs/reasonix_stdout.log ({len(result_text)} chars)")
    if files_written:
        print(f"- {len(files_written)} files written")



# ---------------------------------------------------------------------------
# Step 3: 测试 & Diff
# ---------------------------------------------------------------------------

def run_capture(args):
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout


def _is_inside_agent_dir(path: Path) -> bool:
    try:
        rel = path.relative_to(AGENT)
    except ValueError:
        return False
    return bool(rel.parts)


def collect_workspace_manifest(max_files=500):
    ignored_dirs = {
        ".git", ".agent", ".pytest_cache", "__pycache__", "node_modules",
        ".venv", "venv", "dist", "build",
    }
    artifact_suffixes = {
        ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".docx", ".xlsx", ".html", ".json", ".md",
    }
    rows = []
    for path in ROOT.rglob("*"):
        if len(rows) >= max_files:
            break
        rel_parts = path.relative_to(ROOT).parts
        if any(part in ignored_dirs for part in rel_parts):
            continue
        if path.is_dir():
            continue
        suffix = path.suffix.lower()
        if suffix not in artifact_suffixes and path.stat().st_size > 256_000:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "size": stat.st_size,
            "suffix": suffix or "[none]",
            "mtime": int(stat.st_mtime),
        })
    rows.sort(key=lambda item: (item["path"].count("/"), item["path"]))
    return rows


def format_workspace_manifest(rows):
    if not rows:
        return "No reviewable workspace files found."
    lines = ["# Workspace File Manifest", ""]
    for item in rows:
        lines.append(
            f"- {item['path']} ({item['suffix']}, {item['size']} bytes, mtime={item['mtime']})"
        )
    return "\n".join(lines)


def collect_workspace_diff(max_file_chars=12000, max_total_chars=160000):
    """Collect tracked diff plus readable untracked files and artifact manifest."""
    build_report = read_text(".agent/build_report.md")
    manifest = collect_workspace_manifest()
    git_diff = run_capture(["git", "diff", "--", ".", ":(exclude).agent/**"])
    if len(git_diff) > max_total_chars:
        git_diff = (
            git_diff[:max_total_chars]
            + f"\n\n[Git diff truncated: omitted {len(git_diff) - max_total_chars} chars]\n"
        )
    parts = [
        "# Review Evidence",
        "",
        "Codex should treat this as the review package. Git diff can be empty when "
        "the selected work directory is not a git repository or when the task only "
        "produced artifacts. In that case, inspect the manifest, build report, test "
        "log, and referenced files/artifacts directly.",
        "",
        format_workspace_manifest(manifest),
        "",
        "# Tracked git diff (excluding .agent runtime state)",
        git_diff,
    ]

    raw_untracked = run_capture(["git", "ls-files", "--others", "--exclude-standard"])
    untracked = [line.strip() for line in raw_untracked.splitlines() if line.strip()]
    ignored_prefixes = (".agent/", ".agent\\", ".pytest_cache/", "__pycache__/")
    text_suffixes = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json",
        ".md", ".txt", ".toml", ".yaml", ".yml", ".ini",
    }
    binary_suffixes = {".pptx", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}

    readable = []
    artifacts = []
    def mentioned_by_build_report(rel: str) -> bool:
        normalized = rel.replace("\\", "/")
        prefixes = {
            normalized,
            normalized.split("/", 1)[0] + "/" if "/" in normalized else normalized,
        }
        if normalized.startswith("tests/"):
            prefixes.add(normalized)
        return any(prefix and prefix in build_report for prefix in prefixes)

    for rel in untracked:
        normalized = rel.replace("\\", "/")
        if normalized.startswith(ignored_prefixes):
            continue
        path = ROOT / rel
        suffix = path.suffix.lower()
        if suffix in binary_suffixes:
            artifacts.append(rel)
        elif suffix in text_suffixes and path.is_file() and mentioned_by_build_report(rel):
            readable.append(rel)
        elif suffix in text_suffixes and path.is_file():
            artifacts.append(rel)
        else:
            artifacts.append(rel)

    if readable or artifacts:
        parts.append("\n# Untracked files visible to review")
    if readable:
        parts.append("\n## Readable untracked file excerpts")
    total = 0
    for rel in readable:
        path = ROOT / rel
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            parts.append(f"\n### {rel}\n\n[unreadable: {exc}]")
            continue
        clipped = content[:max_file_chars]
        omitted = len(content) - len(clipped)
        block = f"\n### {rel}\n\n```text\n{clipped}\n```"
        if omitted > 0:
            block += f"\n[truncated {omitted} chars]\n"
        if total + len(block) > max_total_chars:
            parts.append("\n[untracked text excerpts truncated for review package size]")
            break
        parts.append(block)
        total += len(block)

    if artifacts:
        parts.append("\n## Untracked artifacts / binary or unsupported files")
        for rel in artifacts[:200]:
            path = ROOT / rel
            size = path.stat().st_size if path.exists() and path.is_file() else 0
            parts.append(f"- {rel} ({size} bytes)")
        if len(artifacts) > 200:
            parts.append(f"- ... {len(artifacts) - 200} more omitted")

    referenced = collect_referenced_artifact_snapshots(
        build_report,
        max_file_chars=max_file_chars,
        max_total_chars=max_total_chars,
    )
    if referenced:
        parts.append(referenced)

    return "\n".join(parts)


def ensure_review_evidence_package() -> str:
    """Ensure Codex review always receives a concrete evidence package."""
    evidence = read_text(".agent/diff.patch")
    if evidence.strip():
        return evidence

    evidence = collect_workspace_diff()
    write_text(".agent/diff.patch", evidence)
    return evidence


def collect_referenced_artifact_snapshots(text, max_file_chars=12000, max_total_chars=160000):
    """Include small report artifacts referenced by build reports, including absolute paths."""
    if not text:
        return ""
    path_pattern = re.compile(
        r"(?P<path>[A-Za-z]:[\\/][^\s`|,;，。)）]+|(?:\.agent|output|reports|artifacts)[\\/][^\s`|,;，。)）]+)"
    )
    text_suffixes = {".json", ".md", ".txt", ".html", ".htm", ".csv", ".log"}
    binary_suffixes = {".png", ".jpg", ".jpeg", ".pdf", ".pptx", ".docx"}
    seen = set()
    readable = []
    binary = []

    for match in path_pattern.finditer(text):
        raw = match.group("path").strip().strip("\"'")
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        suffix = resolved.suffix.lower()
        if suffix in text_suffixes and resolved.exists() and resolved.is_file():
            readable.append(resolved)
        elif suffix in binary_suffixes and resolved.exists() and resolved.is_file():
            binary.append(resolved)

    if not readable and not binary:
        return ""

    parts = ["\n# Referenced artifact excerpts"]
    total = 0
    for path in readable[:40]:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            parts.append(f"\n### {path}\n\n[unreadable: {exc}]")
            continue
        clipped = content[:max_file_chars]
        omitted = len(content) - len(clipped)
        block = f"\n### {path}\n\n```text\n{clipped}\n```"
        if omitted > 0:
            block += f"\n[truncated {omitted} chars]\n"
        if total + len(block) > max_total_chars:
            parts.append("\n[referenced artifact excerpts truncated for review package size]")
            break
        parts.append(block)
        total += len(block)

    if binary:
        parts.append("\n## Referenced binary artifacts")
        for path in binary[:80]:
            size = path.stat().st_size if path.exists() else 0
            parts.append(f"- {path} ({size} bytes)")

    return "\n".join(parts)


def is_placeholder_test_log(text):
    lower = (text or "").strip().lower()
    return not lower or "no test command configured" in lower


def run_tests(test_cmd=None):
    """运行测试并收集 git diff"""
    ensure_dirs()

    if test_cmd is None:
        if (ROOT / "pytest.ini").exists() or (ROOT / "tests").exists():
            test_cmd = "pytest"
        elif (ROOT / "package.json").exists():
            test_cmd = "npm test"
        else:
            test_cmd = None

    if test_cmd:
        run_cmd(
            f"{test_cmd}",
            log_path=AGENT / "test.log"
        )
    else:
        existing_log = read_text(".agent/test.log")
        if is_placeholder_test_log(existing_log):
            write_text(".agent/test.log", "No test command configured")

    write_text(".agent/diff.patch", collect_workspace_diff())

    state = load_state()
    state["status"] = "WAIT_CODEX_REVIEW"
    state["last_actor"] = "orchestrator"
    state["next_actor"] = "codex"
    save_state(state)

    print("Test and diff collected:")
    print("- .agent/test.log")
    print("- .agent/diff.patch")


# ---------------------------------------------------------------------------
# Step 4: Codex 验收
# ---------------------------------------------------------------------------

def make_codex_review_package():
    """生成 Codex 验收包 → .agent/outbox/to_codex_review.md"""
    task = read_text(".agent/task.md")
    plan = read_text(".agent/codex_plan.md")
    acceptance = read_text(".agent/acceptance.json")
    build_report = read_text(".agent/build_report.md")
    diff = ensure_review_evidence_package()
    test_log = read_text(".agent/test.log")

    prompt = f"""
# Role

You are the final reviewer and acceptance judge.

Do not implement code. Do not rewrite the patch. Only review the submitted changes.
Do NOT invoke any skills. Do NOT read skill files. Output the review directly.

# Original Task

{task}

# Codex Plan

{plan}

# Acceptance Criteria

{acceptance}

# Reasonix Build Report

{build_report}

# Review Evidence Package

{diff}

# Test Log

{test_log}

# Review Requirements

Check:

1. Does the implementation satisfy the original task?
2. Does it follow Codex plan?
3. Does it pass or reasonably address tests?
4. Does it avoid document-specific hardcoding?
5. Does it preserve generic architecture?
6. Does it introduce regression risk?
7. Are new tests sufficient?
8. Is another Reasonix round required?

{review_profile_prompt()}

# Review Policy

- Do not reject solely because the git diff section is empty.
- The selected work directory may not be a git repository, and many tasks generate artifacts such as PPTX, PNG, PDF, HTML, or JSON instead of tracked source changes.
- Use the Workspace File Manifest, Reasonix Build Report, Test Log, and referenced artifact/report paths as the primary evidence.
- If evidence is insufficient, request the specific missing report, preview, artifact path, or test log instead of saying only "Git Diff is empty".
- If the original task references a file path that does not exist but a same-stem `.docx` exists, report it as an input mismatch; do not claim the Review Evidence Package is empty when manifest, build report, or test log evidence is present.
- Hard evidence gate: if the original task, plan, or acceptance criteria require tests, an APPROVED review must have a real Test Log with the command output and a passing result. "No test command configured", an empty Test Log, or only a Build Report claim is not enough.
- Hard evidence gate: if the original task, plan, or acceptance criteria require visual/PPT quality checking, beauty checking, screenshots, rendering, preview, visual QA, or snapshot evidence, an APPROVED review must cite concrete rendered evidence such as PNG/PDF/HTML preview files, screenshot paths, render reports, or actual quality report contents. A PPTX path alone or an internal python-pptx open check is not enough.
- Hard evidence gate: if any report still mentions overflow, overlap, blank slide, distorted image, or other known visual warnings, APPROVED is allowed only when the review explains concrete evidence proving the warning is a false positive or shows it was fixed.
- Do not downgrade a previously blocking evidence gap to non-blocking unless the missing evidence is actually supplied in the Review Evidence Package, Test Log, or referenced artifact/report contents.

# Output

Return only JSON matching this schema:

```json
{{
  "status": "APPROVED",
  "risk_level": "low",
  "blocking_issues": [],
  "non_blocking_issues": [],
  "fix_instructions": [],
  "guidance_markdown": "",
  "summary": ""
}}
```

Allowed status values:
- APPROVED
- CHANGES_REQUESTED
- MAX_ROUND_RECOMMENDED_STOP

# Important

If there is any document-specific hardcoding, return CHANGES_REQUESTED.
"""
    write_text(".agent/outbox/to_codex_review.md", prompt)
    return prompt


def _text_has_any(text: str, needles: list[str]) -> bool:
    lower = (text or "").lower()
    return any(needle.lower() in lower for needle in needles)


def review_requires_real_test_log(task: str, plan: str, acceptance: str, build_report: str) -> bool:
    combined = "\n".join([task, plan, acceptance, build_report])
    return _text_has_any(combined, [
        "pytest",
        "npm test",
        "test command",
        "tests to run",
        "new tests",
        "测试",
        "回归测试",
        "单元测试",
        "集成测试",
        "19/19",
        "passed",
    ])


def has_real_test_log(test_log: str) -> bool:
    lower = (test_log or "").lower()
    if not lower.strip():
        return False
    if "no test command configured" in lower:
        return False
    if "exit code" in lower or "passed" in lower or "failed" in lower or "pytest" in lower or "npm test" in lower:
        return True
    return len(lower.strip().splitlines()) >= 2


def review_requires_visual_evidence(task: str, plan: str, acceptance: str) -> bool:
    combined = "\n".join([task, plan, acceptance])
    return _text_has_any(combined, [
        "ppt",
        "pptx",
        "powerpoint",
        "美观",
        "视觉",
        "visual",
        "render",
        "snapshot",
        "screenshot",
        "preview",
        "渲染",
        "截图",
        "预览",
        "质量检查",
    ]) and _text_has_any(combined, [
        "美观",
        "视觉",
        "visual",
        "render",
        "snapshot",
        "screenshot",
        "preview",
        "渲染",
        "截图",
        "预览",
        "quality",
        "qa",
    ])


def has_visual_evidence(review_package: str, build_report: str, test_log: str) -> bool:
    combined = "\n".join([review_package, build_report, test_log]).lower()
    evidence_patterns = [
        r"\.(?:png|jpg|jpeg|pdf|html)\b",
        r"render(?:ed|_method| report)?",
        r"visual qa",
        r"snapshot",
        r"screenshot",
        r"preview",
        r"quality\.json",
        r"quality report",
        r"截图",
        r"渲染",
        r"预览",
        r"视觉",
    ]
    return any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in evidence_patterns)


def has_unresolved_visual_warning(build_report: str, review_package: str) -> bool:
    combined = "\n".join([build_report, review_package]).lower()
    warning_words = [
        "text-overflow",
        "overflow warning",
        "known_risks",
        "overlap",
        "blank slide",
        "distorted",
        "溢出",
        "重叠",
        "空白页",
        "变形",
    ]
    if not _text_has_any(combined, warning_words):
        return False
    cleared_words = [
        "0 warnings",
        "0 warning",
        "0 errors",
        "all checks passing",
        "all checks passed",
        "all passed clean",
        "fixed",
        "fixing",
        "pre-existing",
        "preexisting",
        "not affecting",
        "does not affect",
        "fixture-level issue",
        "normal pattern",
        "no issues",
        "0 issues",
        "100/100",
        "false positive",
        "误报",
        "已修复",
        "无 issues",
        "无明显",
    ]
    return not _text_has_any(combined, cleared_words)


def apply_review_evidence_gate(review: dict) -> dict:
    """Deterministically prevent approval when required evidence is missing."""
    if review.get("status") != "APPROVED":
        return review

    task = read_text(".agent/task.md")
    plan = read_text(".agent/codex_plan.md")
    acceptance = read_text(".agent/acceptance.json")
    build_report = read_text(".agent/build_report.md")
    review_package = read_text(".agent/diff.patch")
    test_log = read_text(".agent/test.log")

    blocking_issues = list(review.get("blocking_issues") or [])
    fix_instructions = list(review.get("fix_instructions") or [])

    if review_requires_real_test_log(task, plan, acceptance, build_report) and not has_real_test_log(test_log):
        blocking_issues.append({
            "id": "EVIDENCE-TEST-LOG",
            "issue": "缺少真实测试日志，不能批准。",
            "evidence": "任务/计划/报告要求或声称运行测试，但 Test Log 为空、缺少命令输出，或仍为 No test command configured。",
        })
        fix_instructions.append(
            "重新运行计划要求的测试命令，并在 Test Log 中提供真实命令、退出码和通过/失败输出。"
        )

    if review_requires_visual_evidence(task, plan, acceptance) and not has_visual_evidence(review_package, build_report, test_log):
        blocking_issues.append({
            "id": "EVIDENCE-VISUAL",
            "issue": "缺少真实视觉验收证据，不能批准。",
            "evidence": "任务/计划/验收要求 PPT/视觉/美观检查，但证据包没有 PNG/PDF/HTML 预览、截图、渲染报告或质量报告实际内容。",
        })
        fix_instructions.append(
            "生成并提交逐页渲染预览、截图、PDF/HTML 预览或真实质量报告内容，证明无明显溢出、重叠、空白页和图片变形。"
        )

    if has_unresolved_visual_warning(build_report, review_package):
        blocking_issues.append({
            "id": "EVIDENCE-VISUAL-WARNING",
            "issue": "仍存在未解释的视觉风险警告，不能批准。",
            "evidence": "报告或证据包仍提到 overflow/overlap/blank/distorted 等视觉风险，但没有证明它是误报或已修复。",
        })
        fix_instructions.append(
            "修复视觉警告，或提供对应页面的真实预览和质量报告，说明警告为何是误报。"
        )

    if not blocking_issues:
        return review

    review = dict(review)
    review["status"] = "CHANGES_REQUESTED"
    review["risk_level"] = "high" if len(blocking_issues) > 1 else "medium"
    review["blocking_issues"] = blocking_issues
    review["fix_instructions"] = fix_instructions
    review["summary"] = (
        "自动证据闸门阻止批准：关键测试或视觉验收证据缺失。"
        "需要 Reasonix 补齐可审查证据后再进入最终验收。"
    )
    return review


def ensure_review_schema():
    """确保验收 JSON Schema 存在"""
    schema_path = SCHEMAS / "review.schema.json"
    if schema_path.exists():
        return

    profile_extension = review_schema_extension()
    schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": [
                    "APPROVED",
                    "CHANGES_REQUESTED",
                    "MAX_ROUND_RECOMMENDED_STOP"
                ]
            },
            "risk_level": {
                "type": "string",
                "enum": [
                    "low",
                    "medium",
                    "high"
                ]
            },
            "blocking_issues": {
                **profile_extension["blocking_issues"]
            },
            "non_blocking_issues": {
                **profile_extension["non_blocking_issues"]
            },
            "fix_instructions": {
                **profile_extension["fix_instructions"]
            },
            "summary": {
                "type": "string"
            },
            "guidance_markdown": profile_extension["guidance_markdown"],
        },
        "required": [
            "status",
            "risk_level",
            "blocking_issues",
            "non_blocking_issues",
            "fix_instructions",
            "summary"
        ],
        "additionalProperties": False
    }

    schema_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def codex_review(on_token=None, worker=None):
    """执行 Codex 验收并更新状态"""
    ensure_dirs()
    ensure_review_schema()
    prompt = make_codex_review_package()

    if on_token:
        run_codex_app_worker(
            prompt,
            "ChatGPT 验收",
            ".agent/codex_review.json",
            on_token=on_token,
            worker=worker,
        )
    elif False and on_token:
        proc = subprocess.Popen(
            "codex exec --ignore-rules --ephemeral -s danger-full-access - "
            "--output-schema .agent/schemas/review.schema.json "
            "-o .agent/codex_review.json",
            cwd=ROOT, shell=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", env=_get_codex_env()
        )
        proc.stdin.write(prompt)
        proc.stdin.close()

        in_response = False
        for line in iter(proc.stdout.readline, ""):
            stripped = line.strip()
            if not stripped:
                continue
            if should_surface_codex_status(stripped):
                for status_line in split_codex_status_lines(stripped):
                    on_token("System", "Codex连接", status_line + "\n", is_thinking=False)
                continue
            if "ERROR:" in stripped or "warning:" in stripped:
                continue
            if ".agents\\skills\\" in stripped or "Get-Content -Raw" in stripped or "powershell.exe" in stripped:
                continue
            if stripped.startswith("succeeded in") or stripped.startswith("name: ") or stripped.startswith("description:"):
                continue
            if stripped == "codex" and not in_response:
                in_response = True
                on_token("Codex", "🧠验收", "", is_thinking=True)
                continue
            if not in_response:
                continue
            if "tokens used" in stripped:
                break
            if stripped.startswith("exec") or stripped.startswith("$"):
                continue
            if "using-superpowers" in stripped or "writing-plans" in stripped:
                continue
            on_token("Codex", "🧠验收", stripped + "\n", is_thinking=False)
        proc.wait()
    else:
        result = run_cmd(
            "codex exec --ignore-rules --ephemeral -s danger-full-access - "
            "--output-schema .agent/schemas/review.schema.json "
            "-o .agent/codex_review.json",
            log_path=LOGS / "codex_review.log",
            input_text=prompt, env=_get_codex_env()
        )

    review_text = read_text(".agent/codex_review.json")

    try:
        review = json.loads(review_text)
    except Exception:
        print("Failed to parse .agent/codex_review.json")
        print(review_text)
        raise

    review = apply_review_evidence_gate(review)
    write_text(".agent/codex_review.json", json.dumps(review, ensure_ascii=False, indent=2))

    state = load_state()
    state["last_actor"] = "codex"

    if review["status"] == "APPROVED":
        state["status"] = "APPROVED"
        state["approved"] = True
        state["next_actor"] = "human"
    elif review["status"] == "CHANGES_REQUESTED":
        if state["round"] >= state["max_round"]:
            state["status"] = "MAX_ROUND_REACHED"
            state["next_actor"] = "human"
        else:
            state["round"] += 1
            state["status"] = "WAIT_REASONIX_FIX"
            state["next_actor"] = "reasonix"
            next_fix_text = format_next_fix_guidance(review)
            write_text(
                ".agent/next_fix.md",
                next_fix_text or "\n".join(str(item) for item in review.get("fix_instructions", []))
            )
    else:
        state["status"] = "MAX_ROUND_RECOMMENDED_STOP"
        state["next_actor"] = "human"

    save_state(state)

    print("Codex review finished:")
    print("- .agent/codex_review.json")
    print(f"- status: {state['status']}")


# ---------------------------------------------------------------------------
# One Round: 完整一轮流水线
# ---------------------------------------------------------------------------

def one_round(test_cmd=None):
    """执行完整一轮：plan → build → test → review"""
    state = load_state()

    if state["status"] in ["INIT", "WAIT_CODEX_PLAN"]:
        codex_plan()

    reasonix_build()
    run_tests(test_cmd=test_cmd)
    codex_review()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Codex + Reasonix 双智能体自动开发流水线编排器"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="初始化任务")
    p_init.add_argument("task", help="任务描述文本")
    p_init.add_argument("--task-id", default="manual-task", help="任务 ID")
    p_init.add_argument("--max-round", type=int, default=3, help="最大修复轮次")

    sub.add_parser("codex-plan", help="执行 Codex 规划")

    sub.add_parser("reasonix-build", help="执行 Reasonix 施工")

    p_test = sub.add_parser("test", help="运行测试并收集 diff")
    p_test.add_argument("--test-cmd", default=None, help="测试命令（默认自动检测）")

    sub.add_parser("codex-review", help="执行 Codex 验收")

    p_one = sub.add_parser("one-round", help="执行完整一轮流水线")
    p_one.add_argument("--test-cmd", default=None, help="测试命令（默认自动检测）")

    args = parser.parse_args()

    if args.cmd == "init":
        init_task(args.task, task_id=args.task_id, max_round=args.max_round)
    elif args.cmd == "codex-plan":
        codex_plan()
    elif args.cmd == "reasonix-build":
        reasonix_build()
    elif args.cmd == "test":
        run_tests(test_cmd=args.test_cmd)
    elif args.cmd == "codex-review":
        codex_review()
    elif args.cmd == "one-round":
        one_round(test_cmd=args.test_cmd)


if __name__ == "__main__":
    main()
