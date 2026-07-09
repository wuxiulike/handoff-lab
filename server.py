#!/usr/bin/env python3
"""Handoff Lab local planner-worker monitor."""

import json
import os
import re
import sys
import time
import threading
import subprocess
import uuid
from pathlib import Path

from flask import Flask, Response, request, render_template, stream_with_context, send_file, redirect
from guards.ambiguity_guard import is_underspecified

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
AUTH_FILE = ROOT / ".agent" / "auth.json"
SESSION_FILE = ROOT / ".agent" / "session.json"
WORKSPACE_FILE = ROOT / ".agent" / "workspace.json"
CONFIG_FILE = ROOT / ".agent" / "model_config.json"
QA_WATCH_FILE = ROOT / ".agent" / "qa_watch.json"
# session.json 防膨胀：按大小 + 单对话轮次双触发归档
SESSION_MAX_BYTES = 1_000_000      # session.json 软上限（~1MB）
SESSION_MAX_TURNS = 200            # 单个 conversation 的 turns 上限
ARCHIVE_DIR = ROOT / ".agent" / "archive"
AUTH_MODES = {"ask", "allow", "deny", "yolo"}
# Pipeline 显式状态机（server 层循环控制状态；不写入 state.json）
# state.json 的状态写入仍由 tools/ai_flow.py 的各 step 函数负责
PIPELINE_RUNNING = "running"        # 正常进行中（含 CHANGES_REQUESTED 需继续下一轮）
PIPELINE_APPROVED = "approved"      # Codex 验收通过
PIPELINE_MAX_ROUND = "max_round"    # 达到最大轮次
PIPELINE_ERROR = "error"            # 某阶段抛异常
PIPELINE_STOPPED = "stopped"        # 用户请求停止
CODEX_STAGE_TIMEOUT_SECONDS = 180
CODEX_FALLBACK_TIMEOUT_SECONDS = int(os.environ.get("CODEX_FALLBACK_TIMEOUT_SECONDS", "600"))
CODEX_FALLBACK_FAILURE_THRESHOLD = 3
DEEPSEEK_MODELS = {
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-reasoner",
}
DEEPSEEK_CONTEXT_LIMIT = 1_000_000
CODEX_CONTEXT_LIMIT = 196_000

from tools.ai_flow import (
    init_task, codex_plan, reasonix_build, run_tests, codex_review,
    load_state, ensure_dirs, _get_codex_env, set_workspace_root,
    read_text, write_text, collect_workspace_manifest
)
from tools.codex_app_worker import CodexAppWorker

app = Flask(__name__)


def get_host() -> str:
    return os.environ.get("HANDOFF_LAB_HOST", "127.0.0.1")


def get_port() -> int:
    raw = os.environ.get("HANDOFF_LAB_PORT", "51514")
    try:
        port = int(raw)
    except ValueError:
        raise ValueError("HANDOFF_LAB_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("HANDOFF_LAB_PORT must be between 1 and 65535")
    return port


def _is_path_inside(child: Path, parent: Path) -> bool:
    child = child.resolve()
    parent = parent.resolve()
    return child == parent or parent in child.parents


def validate_workspace_root(path: Path) -> Path:
    path = path.expanduser()
    if path.exists() and not path.is_dir():
        raise ValueError("workspace path is not a directory")
    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if resolved == resolved.parent:
        raise ValueError("workspace path cannot be a filesystem root")

    dangerous = []
    for value in (
        os.environ.get("SystemRoot"),
        os.environ.get("WINDIR"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ):
        if value:
            dangerous.append(Path(value).resolve())
    dangerous.extend([
        Path("C:/Windows/System32").resolve(),
        Path("C:/ProgramData").resolve(),
    ])
    for blocked in dangerous:
        if _is_path_inside(resolved, blocked):
            raise ValueError(f"workspace path is inside protected system directory: {blocked}")
    return resolved


def load_workspace_root() -> Path:
    default_root = ROOT
    config_file = WORKSPACE_FILE
    if not config_file.exists():
        set_workspace_root(default_root)
        return default_root
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        path = Path(data.get("path") or default_root).expanduser().resolve()
    except (OSError, json.JSONDecodeError):
        path = default_root
    if not path.exists() or not path.is_dir():
        path = default_root
    set_workspace_root(path)
    return path


def save_workspace_root(path: Path) -> Path:
    path = validate_workspace_root(path)
    config_file = WORKSPACE_FILE
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps({"path": str(path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    set_workspace_root(path)
    close_codex_app_worker()
    return path


load_workspace_root()


def load_auth():
    if not AUTH_FILE.exists():
        return {"mode": "ask"}
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mode": "ask"}
    mode = data.get("mode", "ask")
    if mode not in AUTH_MODES:
        mode = "ask"
    return {"mode": mode}


def save_auth(auth):
    mode = auth.get("mode", "ask")
    if mode not in AUTH_MODES:
        raise ValueError(f"invalid auth mode: {mode}")
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(
        json.dumps({"mode": mode}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return {"mode": mode}


def is_authorized_for_pipeline():
    return load_auth()["mode"] in {"allow", "yolo"}


def apply_auth_env():
    mode = load_auth()["mode"]
    os.environ["REASONIX_YOLO"] = "1" if mode == "yolo" else "0"
    return mode


def remember_pending_start(task_text: str, max_round: int, direct_reasonix: bool):
    global _pending_start_request
    _pending_start_request = {
        "task": task_text,
        "max_round": max_round,
        "direct_reasonix": direct_reasonix,
        "workspace": str(load_workspace_root()),
        "task_excerpt": task_text.strip()[:240],
        "created_at": time.time(),
    }
    emit("auth_request", {
        "action": "start",
        "mode": load_auth()["mode"],
        "workspace": _pending_start_request["workspace"],
        "task_excerpt": _pending_start_request["task_excerpt"],
        "message": "Codex/Reasonix execution requires authorization.",
    })
    return _pending_start_request


def load_model_config():
    defaults = {
        "openai_profile": "",
        "openai_model": "",
        "openai_reasoning": "default",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-v4-pro",
        "deepseek_api_key_set": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "vision_base_url": "https://api.xiaomimimo.com/v1",
        "vision_model": "mimo-v2.5",
        "vision_api_key_set": bool(os.environ.get("VISION_API_KEY") or os.environ.get("MIMO_API_KEY")),
    }
    if not CONFIG_FILE.exists():
        return defaults
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    return {
        "openai_profile": data.get("openai_profile", ""),
        "openai_model": data.get("openai_model", defaults["openai_model"]),
        "openai_reasoning": data.get("openai_reasoning", defaults["openai_reasoning"]),
        "deepseek_base_url": data.get("deepseek_base_url", defaults["deepseek_base_url"]),
        "deepseek_model": data.get("deepseek_model", defaults["deepseek_model"]),
        "deepseek_api_key_set": bool(data.get("deepseek_api_key")) or defaults["deepseek_api_key_set"],
        "vision_base_url": data.get("vision_base_url", defaults["vision_base_url"]),
        "vision_model": data.get("vision_model", defaults["vision_model"]),
        "vision_api_key_set": bool(data.get("vision_api_key")) or defaults["vision_api_key_set"],
    }


def save_model_config(data):
    existing = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    if "openai_profile" in data:
        existing["openai_profile"] = data.get("openai_profile", "").strip()
    if "openai_model" in data:
        existing["openai_model"] = data.get("openai_model", "").strip()
    if "openai_reasoning" in data:
        existing["openai_reasoning"] = data.get("openai_reasoning", "default").strip() or "default"
    if "deepseek_base_url" in data:
        existing["deepseek_base_url"] = data.get("deepseek_base_url", "").strip() or "https://api.deepseek.com"
    if "deepseek_model" in data:
        model = data.get("deepseek_model", "").strip() or "deepseek-v4-pro"
        if model not in DEEPSEEK_MODELS:
            raise ValueError("unsupported deepseek model")
        existing["deepseek_model"] = model
    if data.get("deepseek_api_key"):
        existing["deepseek_api_key"] = data["deepseek_api_key"].strip()
    if "vision_base_url" in data:
        existing["vision_base_url"] = data.get("vision_base_url", "").strip() or "https://api.xiaomimimo.com/v1"
    if "vision_model" in data:
        existing["vision_model"] = data.get("vision_model", "").strip() or "mimo-v2.5"
    if data.get("vision_api_key"):
        existing["vision_api_key"] = data["vision_api_key"].strip()
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_model_config()


def apply_model_config_env():
    if not CONFIG_FILE.exists():
        return
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if data.get("deepseek_api_key"):
        os.environ["DEEPSEEK_API_KEY"] = data["deepseek_api_key"]
    if data.get("deepseek_base_url"):
        os.environ["DEEPSEEK_BASE_URL"] = data["deepseek_base_url"]
    if data.get("openai_profile"):
        os.environ["OPENAI_PROFILE"] = data["openai_profile"]
    if data.get("openai_model"):
        os.environ["OPENAI_MODEL"] = data["openai_model"]
    if data.get("openai_reasoning"):
        os.environ["OPENAI_REASONING"] = data["openai_reasoning"]
    if data.get("deepseek_model"):
        os.environ["REASONIX_MODEL"] = data["deepseek_model"]
    if data.get("vision_api_key"):
        os.environ["VISION_API_KEY"] = data["vision_api_key"]
        os.environ["MIMO_API_KEY"] = data["vision_api_key"]
    if data.get("vision_base_url"):
        os.environ["VISION_BASE_URL"] = data["vision_base_url"]
        os.environ["MIMO_BASE_URL"] = data["vision_base_url"]
    if data.get("vision_model"):
        os.environ["VISION_MODEL"] = data["vision_model"]
        os.environ["MIMO_MODEL"] = data["vision_model"]


def _new_conversation(
    title: str = "新对话",
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    now = time.time()
    return {
        "id": conversation_id or f"chat-{int(now * 1000)}-{uuid.uuid4().hex[:8]}",
        "title": title,
        "workspace": str(workspace or load_workspace_root()),
        "created_at": now,
        "updated_at": now,
        "turns": [],
        "events": [],
    }


def _conversation_title(conversation: dict) -> str:
    title = (conversation.get("title") or "").strip()
    if title and title != "新对话":
        return title
    for turn in conversation.get("turns", []):
        text = (turn.get("text") or "").strip()
        if text:
            return text[:48]
    return title or "新对话"


def _active_conversation(session: dict) -> dict:
    conversations = session.setdefault("conversations", [])
    active_id = session.get("active_id")
    for conversation in conversations:
        if conversation.get("id") == active_id:
            return conversation
    if conversations:
        session["active_id"] = conversations[0].get("id")
        return conversations[0]
    conversation = _new_conversation()
    conversations.append(conversation)
    session["active_id"] = conversation["id"]
    return conversation


_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s`\"'<>|]+")
_PATH_TRAILING_CHARS = ".,;:!?)，。；：！？）】》"
_PROJECT_MARKERS = {
    ".agent",
    ".git",
    "AGENTS.md",
    "HANDOFF.md",
    "package.json",
    "server.py",
    "pyproject.toml",
    "requirements.txt",
}
_PROJECT_CHILD_DIRS = {
    "docx_to_ppt",
    "guards",
    "orchestrator",
    "src",
    "tests",
    "tools",
}


def _iter_conversation_text(conversation: dict):
    yield conversation.get("workspace") or ""
    yield conversation.get("title") or ""
    for turn in conversation.get("turns", []):
        yield turn.get("text") or ""
        for message in turn.get("messages", []):
            yield message.get("text") or ""
    for event in conversation.get("events", []):
        yield event.get("summary") or ""
        yield event.get("text") or ""
        data = event.get("data")
        if isinstance(data, str):
            yield data
        elif isinstance(data, dict):
            yield json.dumps(data, ensure_ascii=False)


def _extract_absolute_paths(text: str):
    for match in _WINDOWS_PATH_RE.finditer(text or ""):
        raw = match.group(0).rstrip(_PATH_TRAILING_CHARS)
        raw = raw.replace("/", "\\")
        yield Path(raw)


def _existing_dir_candidate(path: Path) -> Path | None:
    try:
        if path.exists():
            return path.resolve() if path.is_dir() else path.parent.resolve()
    except OSError:
        return None
    return None


def _workspace_candidate_from_path(path: Path) -> tuple[Path, int] | None:
    parts = list(path.parts)
    lower_parts = [part.lower() for part in parts]
    score = 0

    if ".agent" in lower_parts:
        index = lower_parts.index(".agent")
        if index > 0:
            path = Path(*parts[:index])
            score += 60
    elif parts and parts[-1] in _PROJECT_MARKERS:
        path = path.parent
        score += 45
    else:
        for child_dir in _PROJECT_CHILD_DIRS:
            if child_dir in lower_parts:
                index = lower_parts.index(child_dir)
                if index > 0:
                    path = Path(*parts[:index])
                    score += 35
                break

    existing = _existing_dir_candidate(path)
    if existing is None:
        return None
    if existing == existing.parent:
        return None

    try:
        if existing == ROOT.resolve():
            score -= 25
        else:
            score += 25
    except OSError:
        pass

    for marker in _PROJECT_MARKERS:
        if (existing / marker).exists():
            score += 20
            break
    for child_dir in _PROJECT_CHILD_DIRS:
        if (existing / child_dir).is_dir():
            score += 10
            break
    return existing, score


def infer_conversation_workspace(conversation: dict) -> Path | None:
    candidates: dict[str, tuple[Path, int]] = {}
    for text in _iter_conversation_text(conversation):
        for path in _extract_absolute_paths(text):
            candidate = _workspace_candidate_from_path(path)
            if candidate is None:
                continue
            workspace, score = candidate
            key = str(workspace).lower()
            old = candidates.get(key)
            if old is None or score > old[1]:
                candidates[key] = (workspace, score)
    if not candidates:
        return None
    return sorted(candidates.values(), key=lambda item: item[1], reverse=True)[0][0]


def _workspace_needs_inference(raw_workspace: str | None) -> bool:
    if not raw_workspace:
        return True
    try:
        path = Path(raw_workspace)
        if not path.exists() or not path.is_dir():
            return True
        if path.resolve() == path.resolve().parent:
            return True
        if path.resolve() == ROOT.resolve():
            return True
    except OSError:
        return True
    return "pytest-of-" in raw_workspace.replace("\\", "/").lower()


def resolve_conversation_workspace(conversation: dict) -> Path:
    raw_workspace = conversation.get("workspace")
    if _workspace_needs_inference(raw_workspace):
        inferred = infer_conversation_workspace(conversation)
        if inferred is not None:
            conversation["workspace"] = str(inferred)
            return inferred
    try:
        path = Path(conversation.get("workspace") or ROOT).expanduser().resolve()
        if path == path.parent:
            conversation["workspace"] = str(ROOT)
            return ROOT.resolve()
        return path
    except OSError:
        conversation["workspace"] = str(ROOT)
        return ROOT.resolve()


def normalize_session(data: dict | None) -> dict:
    if not isinstance(data, dict):
        data = {}
    data.setdefault("mode", "dev_loop")

    if "conversations" not in data:
        turns = data.get("turns") if isinstance(data.get("turns"), list) else []
        events = data.get("events") if isinstance(data.get("events"), list) else []
        workspace = data.get("workspace")
        conversations = []
        ordered_turns = sorted(turns, key=lambda item: item.get("ts", 0))
        ordered_events = sorted(events, key=lambda item: item.get("ts", 0))

        if ordered_turns:
            for index, turn in enumerate(ordered_turns):
                start_ts = turn.get("ts", time.time())
                end_ts = (
                    ordered_turns[index + 1].get("ts", float("inf"))
                    if index + 1 < len(ordered_turns)
                    else float("inf")
                )
                scoped_events = [
                    event for event in ordered_events
                    if start_ts <= event.get("ts", 0) < end_ts
                ]
                conversation = _new_conversation(
                    title=(turn.get("text") or "历史对话").strip()[:48] or "历史对话",
                    workspace=workspace,
                    conversation_id=f"legacy-{int(start_ts * 1000)}-{index}",
                )
                conversation["turns"] = [turn]
                conversation["events"] = scoped_events
                conversation["created_at"] = start_ts
                conversation["updated_at"] = max(
                    [start_ts] + [event.get("ts", start_ts) for event in scoped_events]
                )
                conversations.append(conversation)
        else:
            conversation = _new_conversation(title="新对话", workspace=workspace)
            conversation["events"] = events
            conversations.append(conversation)

        active = conversations[-1]
        data = {
            "mode": data.get("mode", "dev_loop"),
            "active_id": active["id"],
            "conversations": conversations,
        }

    conversations = data.setdefault("conversations", [])
    if (
        len(conversations) == 1
        and (conversations[0].get("title") or "").strip() == "历史对话"
        and len(conversations[0].get("turns", [])) > 1
    ):
        legacy = conversations[0]
        ordered_turns = sorted(legacy.get("turns", []), key=lambda item: item.get("ts", 0))
        ordered_events = sorted(legacy.get("events", []), key=lambda item: item.get("ts", 0))
        split_conversations = []
        for index, turn in enumerate(ordered_turns):
            start_ts = turn.get("ts", time.time())
            end_ts = (
                ordered_turns[index + 1].get("ts", float("inf"))
                if index + 1 < len(ordered_turns)
                else float("inf")
            )
            scoped_events = [
                event for event in ordered_events
                if start_ts <= event.get("ts", 0) < end_ts
            ]
            conversation = _new_conversation(
                title=(turn.get("text") or "历史对话").strip()[:48] or "历史对话",
                workspace=legacy.get("workspace"),
                conversation_id=f"{legacy.get('id', 'legacy')}-split-{int(start_ts * 1000)}-{index}",
            )
            conversation["turns"] = [turn]
            conversation["events"] = scoped_events
            conversation["created_at"] = start_ts
            conversation["updated_at"] = max(
                [start_ts] + [event.get("ts", start_ts) for event in scoped_events]
            )
            split_conversations.append(conversation)
        data["conversations"] = split_conversations
        data["active_id"] = split_conversations[-1]["id"]
        conversations = split_conversations

    for conversation in conversations:
        conversation.setdefault("id", f"chat-{uuid.uuid4().hex[:8]}")
        conversation.setdefault("title", "新对话")
        conversation.setdefault("workspace", str(load_workspace_root()))
        conversation.setdefault("created_at", conversation.get("ts", time.time()))
        conversation.setdefault("updated_at", conversation.get("created_at", time.time()))
        conversation.setdefault("turns", [])
        conversation.setdefault("events", [])
        resolve_conversation_workspace(conversation)

    active = _active_conversation(data)
    data["turns"] = active.setdefault("turns", [])
    data["events"] = active.setdefault("events", [])
    return data


def load_session():
    if not SESSION_FILE.exists():
        return normalize_session(None)
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return normalize_session(None)
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    normalized = normalize_session(data)
    after = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    if before != after:
        save_session(normalized)
    return normalized


def _archive_oversized_session(session: dict) -> dict:
    """当 session.json 超过大小或单对话轮次阈值时，把最旧的非活跃对话归档到磁盘。

    归档策略：按 updated_at 升序（最旧优先），逐个把完整 conversation 写入
    .agent/archive/YYYY-MM/<conversation_id>.json，并从会话中移除、登记到
    session["archived"] 元数据。永不归档当前 active conversation。
    """
    conversations = session.get("conversations", [])
    if not conversations:
        return session

    active_id = session.get("active_id")
    needs_archive = False
    try:
        size = len(json.dumps(session, ensure_ascii=False))
    except (TypeError, ValueError):
        size = 0
    if size > SESSION_MAX_BYTES:
        needs_archive = True
    if not needs_archive:
        for conversation in conversations:
            if len(conversation.get("turns", [])) > SESSION_MAX_TURNS:
                needs_archive = True
                break
    if not needs_archive:
        return session

    # 按更新时间升序，最旧优先归档；活跃会话永远排除
    candidates = [
        conv for conv in conversations
        if conv.get("id") != active_id
    ]
    candidates.sort(
        key=lambda c: c.get("updated_at") or c.get("created_at") or 0
    )

    archived_meta = session.setdefault("archived", [])
    if not isinstance(archived_meta, list):
        archived_meta = []
        session["archived"] = archived_meta
    now = time.time()
    year_month = time.strftime("%Y-%m", time.localtime(now))

    archived_ids = set()
    for conversation in candidates:
        # 达标即停：体积低于阈值且无超长对话
        try:
            size = len(json.dumps(session, ensure_ascii=False))
        except (TypeError, ValueError):
            size = 0
        too_big = size > SESSION_MAX_BYTES
        too_long = any(
            len(conv.get("turns", [])) > SESSION_MAX_TURNS
            for conv in session.get("conversations", [])
            if conv.get("id") != active_id
        )
        if not too_big and not too_long:
            break

        conv_id = conversation.get("id") or f"chat-{uuid.uuid4().hex[:8]}"
        # 写入归档（完整保留 conversation）
        archive_subdir = ARCHIVE_DIR / year_month
        archive_subdir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_subdir / f"{conv_id}.json"
        try:
            archive_path.write_text(
                json.dumps(conversation, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError:
            # 归档写盘失败则中止，宁可 session 稍大也不要丢对话
            break

        # 从会话列表移除
        session["conversations"] = [
            c for c in session.get("conversations", [])
            if c.get("id") != conv_id
        ]
        archived_ids.add(conv_id)
        archived_meta.append({
            "id": conv_id,
            "title": (conversation.get("title") or "").strip()[:48] or "历史对话",
            "workspace": conversation.get("workspace") or "",
            "turn_count": len(conversation.get("turns", [])),
            "archived_at": now,
            "archive_path": str(archive_path.relative_to(ROOT)) if _is_path_inside(archive_path, ROOT) else str(archive_path),
        })

    return session


def save_session(session):
    session = normalize_session(session)
    session = _archive_oversized_session(session)
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return session


def create_conversation(title: str = "新对话", workspace: str | None = None):
    session = load_session()
    conversation = _new_conversation(title=title, workspace=workspace)
    session.setdefault("conversations", []).append(conversation)
    session["active_id"] = conversation["id"]
    return save_session(session), conversation


def activate_conversation(conversation_id: str):
    session = load_session()
    for conversation in session.get("conversations", []):
        if conversation.get("id") == conversation_id:
            session["active_id"] = conversation_id
            workspace = resolve_conversation_workspace(conversation)
            saved = save_session(session)
            try:
                save_workspace_root(workspace)
            except (OSError, ValueError):
                conversation["workspace"] = str(ROOT)
                saved = save_session(session)
                save_workspace_root(ROOT)
            return saved, conversation
    return None, None


def sync_workspace_to_active_conversation() -> Path:
    """把 active conversation 缓存的 workspace 推送到权威源（workspace.json）。

    方向：conversation["workspace"] → workspace.json。用于会话切换后恢复
    该会话对应的工作目录。
    """
    session = load_session()
    conversation = _active_conversation(session)
    workspace = resolve_conversation_workspace(conversation)
    try:
        saved_workspace = save_workspace_root(workspace)
        if conversation.get("workspace") != str(saved_workspace):
            conversation["workspace"] = str(saved_workspace)
            conversation["updated_at"] = time.time()
            save_session(session)
        return saved_workspace
    except (OSError, ValueError):
        pass
    workspace = load_workspace_root()
    conversation["workspace"] = str(workspace)
    conversation["updated_at"] = time.time()
    save_session(session)
    return workspace


def update_active_workspace(workspace: Path):
    """把权威源（workspace.json）已确定的工作目录回写到 active conversation 缓存。

    方向：workspace.json → conversation["workspace"]。在用户通过 /api/workspace
    主动切换目录后调用，确保 active conversation 的缓存与权威源一致。
    """
    session = load_session()
    conversation = _active_conversation(session)
    conversation["workspace"] = str(workspace)
    conversation["updated_at"] = time.time()
    return save_session(session)


def workspace_consistency() -> dict:
    """检查工作目录三个来源是否一致（workspace.json / ai_flow.ROOT / active conversation）。

    workspace.json 是唯一权威源；其余两者应与之保持一致。返回不一致项的诊断信息，
    供调试与回归测试使用。返回 {"consistent": bool, "workspace_json": str,
    "ai_flow_root": str, "active_conversation": str}。

    注意：直接从 workspace.json 读取权威值（不调用 load_workspace_root），避免
    其内置的 set_workspace_root 副作用在读取时悄悄“自愈”漂移，从而漏报。
    """
    from tools import ai_flow

    config_file = WORKSPACE_FILE
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            workspace_json_path = str(Path(data.get("path") or ROOT).expanduser().resolve())
        except (OSError, json.JSONDecodeError):
            workspace_json_path = str(ROOT.resolve())
    else:
        workspace_json_path = str(ROOT.resolve())

    ai_flow_root = str(getattr(ai_flow, "ROOT"))
    session = load_session()
    conversation = _active_conversation(session)
    active_conv_ws = conversation.get("workspace", "")
    consistent = (
        workspace_json_path == ai_flow_root
        and (not active_conv_ws or active_conv_ws == workspace_json_path)
    )
    return {
        "consistent": consistent,
        "workspace_json": workspace_json_path,
        "ai_flow_root": ai_flow_root,
        "active_conversation": active_conv_ws,
    }


def append_session_turn(role: str, text: str):
    session = load_session()
    conversation = _active_conversation(session)
    turns = conversation.setdefault("turns", [])
    turns.append({
        "role": role,
        "text": text,
        "ts": time.time(),
        "messages": [],
    })
    if len(turns) == 1:
        conversation["title"] = (text or "新对话").strip()[:48] or "新对话"
    conversation["workspace"] = str(load_workspace_root())
    conversation["updated_at"] = time.time()
    return save_session(session)


def append_session_message(model: str, role: str, text: str, is_thinking: bool = False):
    if not text:
        return load_session()
    session = load_session()
    conversation = _active_conversation(session)
    turns = conversation.setdefault("turns", [])
    if not turns:
        turns.append({
            "role": "system",
            "text": "Recovered session",
            "ts": time.time(),
            "messages": [],
        })
    current = turns[-1]
    current.setdefault("messages", []).append({
        "model": model,
        "role": role,
        "text": text,
        "is_thinking": is_thinking,
        "ts": time.time(),
    })
    conversation["updated_at"] = time.time()
    return save_session(session)


def append_session_event(kind: str, summary: str):
    session = load_session()
    conversation = _active_conversation(session)
    conversation.setdefault("events", []).append({
        "kind": kind,
        "summary": summary,
        "ts": time.time(),
    })
    conversation["updated_at"] = time.time()
    return save_session(session)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


def context_metrics(session=None):
    session = session or load_session()
    conversation = _active_conversation(session)
    turns = conversation.get("turns", [])
    events = conversation.get("events", [])
    text_parts = [turn.get("text", "") for turn in turns]
    for turn in turns:
        text_parts.extend(message.get("text", "") for message in turn.get("messages", []))
    text_parts.extend(event.get("summary", "") for event in events)
    estimated_tokens = estimate_tokens("\n".join(text_parts))
    return {
        "turn_count": len(turns),
        "event_count": len(events),
        "estimated_tokens": estimated_tokens,
        "deepseek": {
            "limit": DEEPSEEK_CONTEXT_LIMIT,
            "percent": round(estimated_tokens / DEEPSEEK_CONTEXT_LIMIT * 100, 2),
        },
        "codex": {
            "limit": CODEX_CONTEXT_LIMIT,
            "percent": round(estimated_tokens / CODEX_CONTEXT_LIMIT * 100, 2),
        },
    }


def build_history_conversations(session=None):
    session = session or load_session()
    conversations = []
    active_id = session.get("active_id")
    for index, conversation in enumerate(session.get("conversations", [])):
        turns = conversation.get("turns", [])
        events = conversation.get("events", [])
        messages = []
        for turn in turns:
            messages.extend(turn.get("messages", []))
        first_turn = turns[0] if turns else {}
        conversations.append({
            "index": index,
            "id": conversation.get("id"),
            "active": conversation.get("id") == active_id,
            "role": first_turn.get("role", "session"),
            "title": _conversation_title(conversation),
            "text": _conversation_title(conversation),
            "workspace": conversation.get("workspace") or "",
            "ts": conversation.get("updated_at") or conversation.get("created_at") or 0,
            "created_at": conversation.get("created_at") or 0,
            "updated_at": conversation.get("updated_at") or 0,
            "turns": turns,
            "messages": messages,
            "message_count": len(messages),
            "events": events,
            "event_count": len(events),
            "turn_count": len(turns),
        })
    return sorted(conversations, key=lambda item: item.get("updated_at") or item.get("ts") or 0, reverse=True)


def archived_meta(session=None):
    """返回已归档对话的只读元数据列表（供前端展示“已归档 N 条”）。"""
    session = session or load_session()
    archived = session.get("archived", [])
    if not isinstance(archived, list):
        return []
    return [
        {
            "id": item.get("id"),
            "title": item.get("title", "历史对话"),
            "workspace": item.get("workspace", ""),
            "turn_count": item.get("turn_count", 0),
            "archived_at": item.get("archived_at", 0),
        }
        for item in archived
        if isinstance(item, dict)
    ]


def is_underspecified_task(task_text: str) -> bool:
    return is_underspecified(task_text or "")[0]


# ── SSE 事件 ────────────────────────────────────────────
_event_queue: list[dict] = []
_event_lock = threading.Lock()
_qa_event_log: list[dict] = []
QA_EVENT_LOG_LIMIT = 200
_qa_watch_thread: threading.Thread | None = None
_qa_watch_stop = threading.Event()
_qa_watch_workspace = ""


def emit(event_type: str, data: dict):
    with _event_lock:
        _event_queue.append({
            "event": event_type,
            "data": json.dumps(data, ensure_ascii=False)
        })


def emit_qa_external(event: dict):
    _qa_event_log.append(event)
    del _qa_event_log[:-QA_EVENT_LOG_LIMIT]
    emit("qa_external", event)


QA_WATCH_SUFFIXES = {".md", ".json", ".log", ".txt", ".html", ".docx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".mjs", ".py"}
QA_OUTPUT_SUFFIXES = {".md", ".log", ".txt", ".json"}
QA_OUTPUT_EXCLUDED_NAMES = {
    "session.json",
    "to_codex_review.md",
    "to_codex_plan.md",
}


def collect_qa_workspace_snapshot(workspace: Path, limit: int = 12) -> dict:
    workspace = workspace.expanduser().resolve()
    files = []
    for root_name in (".reasonix", ".agent"):
        root = workspace / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in QA_WATCH_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append({
                "path": str(path.relative_to(workspace)).replace("\\", "/"),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
    files = sorted(files, key=lambda item: item["mtime"], reverse=True)[:limit]

    git_status = ""
    git_dir = workspace / ".git"
    if git_dir.exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(workspace), "status", "--short"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            git_status = result.stdout.strip()[:6000]
        except Exception as exc:
            git_status = f"git status failed: {exc}"

    return {
        "workspace": str(workspace),
        "files": files,
        "git_status": git_status,
    }


def format_qa_workspace_snapshot(snapshot: dict) -> str:
    lines = [f"workspace: {snapshot.get('workspace', '')}"]
    files = snapshot.get("files") or []
    if files:
        lines.append(f"\nrecent Reasonix/Codex evidence files ({len(files)} newest):")
        for item in files:
            lines.append(f"- {item['path']} ({item['size']} bytes)")
    else:
        lines.append("\nNo .reasonix/.agent evidence files found yet.")
    if snapshot.get("git_status"):
        lines.append("\ngit status --short:")
        lines.append(snapshot["git_status"])
    return "\n".join(lines)


def read_reasonix_output_file(path: Path, max_chars: int = 8000) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return f"read failed: {exc}"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    elif len(raw) > 4 and raw[:200].count(b"\x00") > 10:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:] + f"\n\n[前面 {len(text) - max_chars} 字符已省略]"


def collect_reasonix_outputs(workspace: Path, limit: int = 5) -> list[dict]:
    workspace = workspace.expanduser().resolve()
    candidates_by_path = {}
    roots = [
        workspace / ".reasonix" / "evidence",
        workspace / ".reasonix",
        workspace / ".agent",
    ]
    priority_names = {
        "build_report.md": 100,
        "deploy_remote.log": 80,
        "test_backend.log": 75,
        "test_frontend_or_manual.log": 70,
        "generate_evidence.log": 65,
        "progress.json": 60,
    }
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in QA_OUTPUT_SUFFIXES:
                continue
            if path.name in QA_OUTPUT_EXCLUDED_NAMES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > 512_000 and path.name != "reasonix_stdout.log":
                continue
            rel = str(path.relative_to(workspace)).replace("\\", "/")
            priority = priority_names.get(path.name, 10)
            if "evidence" in rel:
                priority += 5
            current = {
                "path": rel,
                "abs_path": str(path),
                "name": path.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "priority": priority,
            }
            existing = candidates_by_path.get(rel)
            if not existing or (current["priority"], current["mtime"]) > (existing["priority"], existing["mtime"]):
                candidates_by_path[rel] = current
    candidates = list(candidates_by_path.values())
    candidates.sort(key=lambda item: (item["priority"], item["mtime"]), reverse=True)
    outputs = []
    for item in candidates[:limit]:
        path = Path(item["abs_path"])
        outputs.append({
            "path": item["path"],
            "size": item["size"],
            "mtime": item["mtime"],
            "content": read_reasonix_output_file(path),
        })
    return outputs


def format_reasonix_output(output: dict) -> str:
    return (
        f"path: {output.get('path', '')}\n"
        f"size: {output.get('size', 0)} bytes\n\n"
        f"{output.get('content', '')}"
    )


def emit_qa_workspace_snapshot(workspace: Path):
    snapshot = collect_qa_workspace_snapshot(workspace)
    emit_qa_external({
        "kind": "workspace_snapshot",
        "label": "Reasonix",
        "title": "Reasonix 工作目录变化",
        "detail": format_qa_workspace_snapshot(snapshot),
        "workspace": snapshot["workspace"],
        "conversation_id": "",
        "ts": time.time(),
    })
    return snapshot


def _run_qa_workspace_watch(workspace: Path, interval: float = 3.0):
    last_digest = ""
    while not _qa_watch_stop.is_set():
        try:
            outputs = collect_reasonix_outputs(workspace)
            digest_payload = {
                "outputs": [
                    {
                        "path": item["path"],
                        "size": item["size"],
                        "mtime_bucket": int(item["mtime"] // 10),
                    }
                    for item in outputs
                ],
            }
            digest = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True)
            if digest != last_digest:
                last_digest = digest
                if outputs:
                    for output in outputs[:3]:
                        emit_qa_external({
                            "kind": "reasonix_output",
                            "label": "Reasonix",
                            "title": f"Reasonix 输出：{output['path']}",
                            "detail": format_reasonix_output(output),
                            "workspace": str(workspace),
                            "conversation_id": "",
                            "ts": time.time(),
                        })
                else:
                    snapshot = collect_qa_workspace_snapshot(workspace)
                    emit_qa_external({
                        "kind": "workspace_snapshot",
                        "label": "Reasonix",
                        "title": "Reasonix 工作目录变化",
                        "detail": format_qa_workspace_snapshot(snapshot),
                        "workspace": snapshot["workspace"],
                        "conversation_id": "",
                        "ts": time.time(),
                    })
        except Exception as exc:
            emit_qa_external({
                "kind": "workspace_watch_error",
                "label": "System",
                "title": "工作目录监控失败",
                "detail": str(exc),
                "workspace": str(workspace),
                "conversation_id": "",
                "ts": time.time(),
            })
        _qa_watch_stop.wait(interval)


def save_qa_watch_state(workspace: str = "", interval: float = 5.0):
    QA_WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    QA_WATCH_FILE.write_text(
        json.dumps({"workspace": workspace, "interval": interval}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_qa_watch_state() -> dict:
    if not QA_WATCH_FILE.exists():
        return {}
    try:
        return json.loads(QA_WATCH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def start_qa_watch_thread(workspace: Path, interval: float = 5.0):
    global _qa_watch_thread, _qa_watch_workspace
    _qa_watch_stop.set()
    _qa_watch_stop.clear()
    _qa_watch_workspace = str(workspace)
    _qa_watch_thread = threading.Thread(
        target=_run_qa_workspace_watch,
        args=(workspace, interval),
        daemon=True,
    )
    _qa_watch_thread.start()
    return _qa_watch_thread


def emit_agent_event(kind: str, actor: str, message: str, **metadata):
    payload = {
        "kind": kind,
        "actor": actor,
        "message": message,
        "ts": time.time(),
    }
    if metadata:
        payload["metadata"] = metadata
    emit("agent_event", payload)


class WorkerSession:
    def __init__(self, actor: str, label: str, workspace: Path):
        self.actor = actor
        self.label = label
        self.workspace = workspace
        self.session_id = f"{actor.lower()}-{int(time.time() * 1000)}"
        self.progress: list[str] = []

    def start(self):
        emit_agent_event(
            "session_started",
            self.actor,
            f"{self.label} started",
            session_id=self.session_id,
            workspace=str(self.workspace),
        )

    def progress_event(self, text: str):
        if not text:
            return
        self.progress.append(text)
        emit_agent_event(
            "progress",
            self.actor,
            text,
            session_id=self.session_id,
        )
        emit("token", {
            "model": self.actor,
            "role": "工作过程",
            "text": text + "\n",
            "is_thinking": False,
        })

    def complete(self, summary: str):
        emit_agent_event(
            "session_completed",
            self.actor,
            summary,
            session_id=self.session_id,
            progress_count=len(self.progress),
        )

    def fail(self, error: Exception):
        emit_agent_event(
            "session_failed",
            self.actor,
            str(error),
            session_id=self.session_id,
        )


def emit_pipeline_token(model: str, role: str, text: str, is_thinking: bool):
    if model == "System" and role == "Codex连接":
        emit("status", {"text": text.strip()})
        return

    emit("token", {
        "model": model,
        "role": role,
        "text": text,
        "is_thinking": is_thinking
    })


def make_recording_token_sink():
    captured = []

    def on_token(model: str, role: str, text: str, is_thinking: bool):
        emit_pipeline_token(model, role, text, is_thinking)
        if model == "System" and role == "Codex杩炴帴":
            return
        if text:
            captured.append({
                "model": model,
                "role": role,
                "text": text,
                "is_thinking": is_thinking,
            })

    return on_token, captured


def save_captured_messages(captured: list[dict]):
    merged = []
    for item in captured:
        if (
            merged
            and merged[-1]["model"] == item["model"]
            and merged[-1]["role"] == item["role"]
            and merged[-1]["is_thinking"] == item["is_thinking"]
        ):
            merged[-1]["text"] += item["text"]
        else:
            merged.append(dict(item))
    for item in merged:
        append_session_message(
            item["model"],
            item["role"],
            item["text"].strip(),
            item["is_thinking"],
        )


def summarize_reasonix_result(max_report_chars: int = 2200) -> str:
    report = read_text(".agent/build_report.md").strip()
    manifest = sorted(
        collect_workspace_manifest(),
        key=lambda item: item.get("mtime", 0),
        reverse=True,
    )
    artifacts = [
        item for item in manifest
        if item.get("suffix") in {".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".html", ".json", ".md"}
    ][:12]
    parts = []
    if report:
        clipped = report[:max_report_chars]
        parts.append(clipped)
        if len(report) > max_report_chars:
            parts.append(f"\n[build_report 已截断，完整内容见 .agent/build_report.md，剩余 {len(report) - max_report_chars} 字符]")
    else:
        parts.append("Reasonix 已结束，但没有写入 .agent/build_report.md。")
    if artifacts:
        parts.append("\n\n最新可审查产物：")
        for item in artifacts:
            parts.append(f"- {item['path']} ({item['size']} bytes)")
    parts.append("\n\n完整执行日志：.agent/logs/reasonix_stdout.log")
    return "\n".join(parts)


def emit_text_stream(model: str, role: str, text: str, delay: float = 0.0):
    for ch in text:
        if _stop_requested.is_set():
            raise DialogueStopped("真实对话已停止")
        emit("token", {
            "model": model,
            "role": role,
            "text": ch,
            "is_thinking": False,
        })
        if delay:
            time.sleep(delay)


# ── 停止控制 ─────────────────────────────────────────────
_stop_requested = threading.Event()
_last_stop_time = 0.0
_current_proc: subprocess.Popen | None = None
_pipeline_thread: threading.Thread | None = None
_real_dialogue_thread: threading.Thread | None = None
_dev_rehearsal_thread: threading.Thread | None = None
_pending_start_request: dict | None = None
_codex_app_worker: CodexAppWorker | None = None


class DialogueStopped(RuntimeError):
    pass


def get_codex_app_worker() -> CodexAppWorker:
    global _codex_app_worker
    if _codex_app_worker is None:
        _codex_app_worker = CodexAppWorker(cwd=load_workspace_root(), env=_get_codex_env())
    return _codex_app_worker


def close_codex_app_worker():
    global _codex_app_worker
    if _codex_app_worker is not None:
        _codex_app_worker.close()
        _codex_app_worker = None


# ── 路由 ────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect("/qa-viewer", code=302)


@app.route("/qa-viewer")
def qa_viewer():
    import datetime
    watch_workspace = _qa_watch_workspace
    if not watch_workspace:
        try:
            watch_workspace = load_qa_watch_state().get("workspace") or ""
        except Exception:
            watch_workspace = ""
    if not watch_workspace:
        watch_workspace = str(load_workspace_root())
    resp = app.make_response(render_template("qa_viewer.html",
        version=datetime.datetime.now().strftime("%H%M%S"),
        watch_workspace=watch_workspace))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/stream")
def stream():
    def generate():
        yield "event: connected\ndata: {}\n\n"
        idx = len(_event_queue)
        while True:
            with _event_lock:
                while idx < len(_event_queue):
                    ev = _event_queue[idx]
                    idx += 1
                    yield f"event: {ev['event']}\ndata: {ev['data']}\n\n"
            time.sleep(0.05)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/api/auth", methods=["GET", "POST"])
def api_auth():
    if request.method == "GET":
        return load_auth()

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "ask")
    if mode not in AUTH_MODES:
        return {"error": "invalid_auth_mode"}, 400
    return save_auth({"mode": mode})


@app.route("/api/model-config", methods=["GET", "POST"])
def api_model_config():
    if request.method == "GET":
        return load_model_config()
    data = request.get_json(silent=True) or {}
    try:
        config = save_model_config(data)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    apply_model_config_env()
    return config


@app.route("/api/test-model", methods=["POST"])
def api_test_model():
    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "")
    apply_model_config_env()
    if provider == "deepseek":
        try:
            from openai import OpenAI

            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not api_key:
                return {"ok": False, "message": "DEEPSEEK_API_KEY not set"}, 400
            config = load_model_config()
            client = OpenAI(api_key=api_key, base_url=config["deepseek_base_url"])
            response = client.chat.completions.create(
                model=config["deepseek_model"],
                messages=[{"role": "user", "content": "Reply OK only."}],
                max_tokens=8,
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            return {"ok": True, "message": content.strip() or "OK"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}, 502

    if provider == "openai":
        try:
            worker = get_codex_app_worker()
            text = worker.ask("Reply OK.").strip()
            return {"ok": True, "message": text[:120] or "OK"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}, 502

    if provider == "vision":
        try:
            from openai import OpenAI

            config = load_model_config()
            api_key = os.environ.get("VISION_API_KEY") or os.environ.get("MIMO_API_KEY") or ""
            if not api_key:
                return {"ok": False, "message": "VISION_API_KEY or MIMO_API_KEY not set"}, 400
            client = OpenAI(api_key=api_key, base_url=config["vision_base_url"])
            response = client.chat.completions.create(
                model=config["vision_model"],
                messages=[{"role": "user", "content": "Reply OK only."}],
                max_tokens=8,
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            return {"ok": True, "message": content.strip() or "OK"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}, 502

    return {"error": "unsupported_provider"}, 400


@app.route("/api/qa-event", methods=["POST"])
def api_qa_event():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    detail = str(data.get("detail") or "").strip()
    if not title and not detail:
        return {"error": "qa_event_empty"}, 400

    event = {
        "kind": str(data.get("kind") or "external").strip()[:80],
        "label": str(data.get("label") or "External").strip()[:80],
        "title": title[:500],
        "detail": detail[:20000],
        "conversation_id": str(data.get("conversation_id") or "").strip()[:160],
        "workspace": str(data.get("workspace") or "").strip()[:500],
        "ts": time.time(),
    }
    emit_qa_external(event)
    return {"status": "ok", "event": event}


@app.route("/api/qa-events", methods=["GET", "DELETE"])
def api_qa_events():
    global _qa_watch_workspace
    if request.method == "DELETE":
        _qa_event_log.clear()
        _qa_watch_stop.set()
        _qa_watch_workspace = ""
        save_qa_watch_state("")
        return {"status": "cleared"}
    return {"events": _qa_event_log[-QA_EVENT_LOG_LIMIT:]}


@app.route("/api/qa-watch", methods=["GET", "POST", "DELETE"])
def api_qa_watch():
    global _qa_watch_thread, _qa_watch_workspace

    if request.method == "GET":
        return {
            "running": bool(_qa_watch_thread and _qa_watch_thread.is_alive()),
            "workspace": _qa_watch_workspace,
        }

    if request.method == "DELETE":
        _qa_watch_stop.set()
        save_qa_watch_state("")
        return {"status": "stopped", "workspace": _qa_watch_workspace}

    data = request.get_json(silent=True) or {}
    raw_workspace = str(data.get("workspace") or "").strip()
    if not raw_workspace:
        return {"error": "workspace_required"}, 400
    workspace = Path(raw_workspace).expanduser()
    if not workspace.exists() or not workspace.is_dir():
        return {"error": "workspace_not_found"}, 400
    workspace = workspace.resolve()
    interval = max(1.0, min(30.0, float(data.get("interval") or 3.0)))

    save_qa_watch_state(str(workspace), interval)
    start_qa_watch_thread(workspace, interval)
    return {"status": "watching", "workspace": str(workspace), "interval": interval}


@app.route("/api/context", methods=["GET"])
def api_context():
    session = load_session()
    return {
        "session": session,
        "metrics": context_metrics(session),
    }


def _file_status(rel_path: str, max_chars: int = 4000) -> dict:
    path = load_workspace_root() / rel_path
    if not path.exists() or not path.is_file():
        return {"path": rel_path, "exists": False}
    stat = path.stat()
    try:
        excerpt = path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except OSError:
        excerpt = ""
    return {
        "path": rel_path,
        "exists": True,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "excerpt": excerpt,
    }


def _is_pipeline_running() -> bool:
    return bool(_pipeline_thread and _pipeline_thread.is_alive())


def _is_terminal_state(state: dict) -> bool:
    if _is_pipeline_running():
        return False
    if state.get("approved"):
        return True
    return state.get("status") in {
        "APPROVED",
        "MAX_ROUND_REACHED",
        "MAX_ROUND_RECOMMENDED_STOP",
        "REASONIX_BUILD_FAILED",
        "WAIT_TEST",
        "WAIT_CODEX_REVIEW",
        "NEEDS_CLARIFICATION",
        "BLOCKED",
    }


@app.route("/api/state", methods=["GET"])
def api_state():
    """Compatibility status endpoint for external skills and Codex threads."""
    workspace = load_workspace_root()
    state = load_state()
    return {
        "workspace": str(workspace),
        "running": _is_pipeline_running(),
        "terminal": _is_terminal_state(state),
        "status": state.get("status", "UNKNOWN"),
        "approved": bool(state.get("approved")),
        "round": state.get("round"),
        "max_round": state.get("max_round"),
        "state": state,
        "build_report": _file_status(".agent/build_report.md"),
        "test_log": _file_status(".agent/test.log", max_chars=2000),
    }


@app.route("/api/health", methods=["GET"])
def api_health():
    return {
        "ok": True,
        "workspace": str(load_workspace_root()),
        "running": _is_pipeline_running(),
    }


@app.route("/api/workspace", methods=["GET", "POST"])
def api_workspace():
    if request.method == "GET":
        workspace = sync_workspace_to_active_conversation()
        return {"path": str(workspace), "tree": _build_file_tree()}

    data = request.get_json(silent=True) or {}
    raw_path = (data.get("path") or "").strip()
    if not raw_path:
        return {"error": "workspace_path_required"}, 400
    try:
        workspace = save_workspace_root(Path(raw_path))
    except OSError as exc:
        return {"error": f"cannot_create_workspace: {exc}"}, 400
    except ValueError as exc:
        return {"error": str(exc)}, 400
    update_active_workspace(workspace)
    tree = _build_file_tree()
    emit("file_update", {"path": str(workspace), "tree": tree})
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    return {"path": str(workspace), "tree": tree}


@app.route("/api/file-tree", methods=["GET"])
def api_file_tree():
    return {"path": str(load_workspace_root()), "tree": _build_file_tree()}


@app.route("/api/history", methods=["GET", "DELETE"])
def api_history():
    if request.method == "DELETE":
        session, conversation = create_conversation(workspace=str(load_workspace_root()))
        session["conversations"] = [conversation]
        session["active_id"] = conversation["id"]
        save_session(session)
        emit("context", {"metrics": context_metrics(), "session": load_session()})
        return {"status": "cleared"}

    session = load_session()
    return {
        "turns": session.get("turns", []),
        "events": session.get("events", []),
        "conversations": build_history_conversations(session),
        "archived": archived_meta(session),
    }


@app.route("/api/history/<int:index>", methods=["DELETE"])
def api_history_item(index: int):
    session = load_session()
    conversations = session.get("conversations", [])
    ordered = build_history_conversations(session)
    if index < 0 or index >= len(ordered):
        return {"error": "history_conversation_not_found"}, 404

    delete_id = ordered[index].get("id")
    conversations = [item for item in conversations if item.get("id") != delete_id]
    if not conversations:
        conversations = [_new_conversation(workspace=str(load_workspace_root()))]
    session["conversations"] = conversations
    if not any(item.get("id") == session.get("active_id") for item in conversations):
        session["active_id"] = conversations[0].get("id")
        try:
            save_workspace_root(Path(conversations[0].get("workspace") or ROOT))
        except (OSError, ValueError):
            conversations[0]["workspace"] = str(ROOT)
            save_workspace_root(ROOT)
    session = save_session(session)
    emit("context", {"metrics": context_metrics(session), "session": session})
    emit("file_update", {"path": str(load_workspace_root()), "tree": _build_file_tree()})
    return {"status": "deleted"}


@app.route("/api/history/<conversation_id>/activate", methods=["POST"])
def api_history_activate(conversation_id: str):
    session, conversation = activate_conversation(conversation_id)
    if not conversation:
        return {"error": "history_conversation_not_found"}, 404
    tree = _build_file_tree()
    emit("context", {"metrics": context_metrics(session), "session": session})
    workspace = str(load_workspace_root())
    emit("file_update", {"path": workspace, "tree": tree})
    conversations = build_history_conversations(session)
    active = next((item for item in conversations if item.get("id") == conversation.get("id")), None)
    return {
        "status": "active",
        "conversation": active or conversation,
        "workspace": workspace,
        "tree": tree,
        "session": session,
        "metrics": context_metrics(session),
    }


@app.route("/api/new-chat", methods=["POST"])
def api_new_chat():
    session, conversation = create_conversation(workspace=str(load_workspace_root()))
    emit("context", {"metrics": context_metrics(session), "session": session})
    emit("file_update", {"path": str(load_workspace_root()), "tree": _build_file_tree()})
    emit("new_chat", {"status": "ready", "conversation_id": conversation["id"]})
    return {"status": "ready", "conversation": conversation}


def resolve_workspace_path(raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("missing path")
    root = load_workspace_root().resolve()
    target = (root / raw_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path outside workspace")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(raw_path)
    return target


@app.route("/api/file", methods=["GET"])
def api_file_preview():
    raw_path = request.args.get("path", "")
    try:
        target = resolve_workspace_path(raw_path)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    except FileNotFoundError:
        return {"error": "file_not_found"}, 404

    suffix = target.suffix.lower()
    root = load_workspace_root().resolve()
    rel = str(target.relative_to(root)).replace("\\", "/")
    text_suffixes = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json",
        ".md", ".txt", ".toml", ".yaml", ".yml", ".ini", ".log", ".patch",
    }
    image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    if suffix in image_suffixes:
        return {"path": rel, "kind": "image", "size": target.stat().st_size}
    if suffix == ".html":
        return {
            "path": rel,
            "kind": "html",
            "size": target.stat().st_size,
            "content": target.read_text(encoding="utf-8", errors="ignore")[:200000],
        }
    if suffix in text_suffixes:
        content = target.read_text(encoding="utf-8", errors="ignore")
        truncated = len(content) > 200000
        return {
            "path": rel,
            "kind": "text",
            "size": target.stat().st_size,
            "truncated": truncated,
            "content": content[:200000],
        }
    return {"path": rel, "kind": "binary", "size": target.stat().st_size}


@app.route("/api/file/raw", methods=["GET"])
def api_file_raw():
    raw_path = request.args.get("path", "")
    try:
        target = resolve_workspace_path(raw_path)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    except FileNotFoundError:
        return {"error": "file_not_found"}, 404
    return send_file(target)


def start_pipeline_from_request(task_text: str, max_round: int, direct_reasonix: bool):
    global _pipeline_thread, _current_proc

    if is_underspecified_task(task_text):
        return {
            "error": "task_underspecified",
            "message": "任务太短或缺少明确对象，请补充要做什么、改哪里、验收标准是什么。"
        }, 400

    # 终止旧流水线
    _stop_requested.set()
    if _current_proc and _current_proc.poll() is None:
        _current_proc.kill()
    _current_proc = None
    _pipeline_thread = None

    _stop_requested.clear()
    session = append_session_turn("user", task_text)
    emit("context", {"metrics": context_metrics(session), "session": session})

    init_task(task_text, max_round=max_round)
    if direct_reasonix:
        prepare_direct_reasonix_packet(task_text, max_round=max_round)
    state = load_state()

    emit("round_start", {"round": 1, "max_round": max_round})

    _pipeline_thread = threading.Thread(target=_run_pipeline, args=(max_round, direct_reasonix), daemon=True)
    _pipeline_thread.start()

    return {
        "status": "started",
        "task_id": state.get("task_id", ""),
        "direct_reasonix": direct_reasonix,
    }


@app.route("/api/start", methods=["POST"])
def api_start():
    load_workspace_root()
    data = request.get_json(silent=True) or {}
    task_text = data.get("task", "")
    max_round = data.get("max_round", 3)
    direct_reasonix = bool(data.get("direct_reasonix") or data.get("skip_plan") or data.get("packet_mode"))

    if not task_text.strip():
        return {"error": "task is empty"}, 400

    auth = load_auth()
    if not is_authorized_for_pipeline():
        if auth["mode"] == "ask":
            remember_pending_start(task_text, max_round, direct_reasonix)
        return {
            "error": "authorization_required",
            "mode": auth["mode"],
            "message": "Codex/Reasonix execution requires authorization."
        }, 403

    return start_pipeline_from_request(task_text, max_round, direct_reasonix)


@app.route("/api/auth/pending-start", methods=["GET", "POST"])
def api_auth_pending_start():
    global _pending_start_request
    if request.method == "GET":
        pending = dict(_pending_start_request or {})
        if pending:
            pending.pop("task", None)
        return {"pending": bool(_pending_start_request), "request": pending}

    data = request.get_json(silent=True) or {}
    decision = data.get("decision", "once")
    if decision not in {"once", "allow", "yolo"}:
        return {"error": "invalid_auth_decision"}, 400
    if not _pending_start_request:
        return {"error": "no_pending_start"}, 404

    pending = _pending_start_request
    _pending_start_request = None
    if decision in {"allow", "yolo"}:
        save_auth({"mode": decision})
    return start_pipeline_from_request(
        pending["task"],
        pending["max_round"],
        pending["direct_reasonix"],
    )


@app.route("/api/crosstalk", methods=["POST"])
def api_crosstalk():
    return start_real_dialogue()


@app.route("/api/real-dialogue", methods=["POST"])
def api_real_dialogue():
    return start_real_dialogue()


@app.route("/api/dev-rehearsal", methods=["POST"])
def api_dev_rehearsal():
    return start_dev_rehearsal()


def start_real_dialogue():
    global _real_dialogue_thread

    load_workspace_root()
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "").strip() or "双智能体协作"
    rounds_count = parse_dialogue_rounds(data.get("rounds", 3))

    auth = load_auth()
    if not is_authorized_for_pipeline():
        return {
            "error": "authorization_required",
            "mode": auth["mode"],
            "message": "Real model dialogue requires CLI/API authorization."
        }, 403

    session = append_session_turn("user", f"真实对话：{topic}")
    emit("context", {"metrics": context_metrics(session), "session": session})
    _stop_requested.clear()

    _real_dialogue_thread = threading.Thread(
        target=_run_real_dialogue,
        args=(topic, rounds_count),
        daemon=True,
    )
    _real_dialogue_thread.start()

    return {"status": "started", "topic": topic, "rounds": rounds_count}


def start_dev_rehearsal():
    global _dev_rehearsal_thread, _current_proc

    load_workspace_root()
    data = request.get_json(silent=True) or {}
    rounds_count = min(3, parse_dialogue_rounds(data.get("rounds", 3)))

    auth = load_auth()
    if not is_authorized_for_pipeline():
        return {
            "error": "authorization_required",
            "mode": auth["mode"],
            "message": "Development rehearsal requires Codex/Reasonix authorization."
        }, 403

    _stop_requested.set()
    if _current_proc and _current_proc.poll() is None:
        _current_proc.kill()
    _current_proc = None

    _stop_requested.clear()
    task_text = f"开发演练：简易命令行记事本，{rounds_count} 轮递增实现"
    session = append_session_turn("user", task_text)
    emit("context", {"metrics": context_metrics(session), "session": session})

    _dev_rehearsal_thread = threading.Thread(
        target=_run_dev_rehearsal,
        args=(rounds_count,),
        daemon=True,
    )
    _dev_rehearsal_thread.start()

    return {"status": "started", "rounds": rounds_count}


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _last_stop_time, _current_proc

    now = time.time()
    if _last_stop_time and now - _last_stop_time < 3:
        if _current_proc and _current_proc.poll() is None:
            _current_proc.kill()
        _stop_requested.set()
        close_codex_app_worker()
        emit_agent_event("pipeline_stopped", "System", "User forced stop")
        emit("stopped", {"reason": "user_force"})
        _last_stop_time = 0
        return {"action": "force_kill"}

    _stop_requested.set()
    close_codex_app_worker()
    _last_stop_time = now
    emit_agent_event("pipeline_stopped", "System", "User requested stop")
    emit("stopped", {"reason": "user_graceful"})
    return {"action": "graceful"}


def parse_dialogue_rounds(value) -> int:
    try:
        rounds_count = int(value)
    except (TypeError, ValueError):
        rounds_count = 3
    return min(20, max(1, rounds_count))


def finish_real_dialogue(summary: str):
    emit("step_done", {
        "step": "pipeline_complete",
        "actor": "System",
        "summary": summary,
    })


def stop_real_dialogue_if_requested() -> bool:
    if not _stop_requested.is_set():
        return False
    emit("stopped", {"reason": "user_graceful"})
    finish_real_dialogue("真实对话已停止")
    return True


def _run_real_dialogue(topic: str, rounds_count: int = 3):
    append_session_event("real_dialogue", f"真实模型对话：{topic}")
    emit("context", {"metrics": context_metrics(), "session": load_session()})

    emit("token", {
        "model": "System",
        "role": "说明",
        "text": "真实对话会调用 Codex CLI 和 DeepSeek API；如果网络重连，会显示连接状态。\n",
        "is_thinking": False,
    })

    transcript = []
    rounds = build_dialogue_round_prompts(topic, rounds=rounds_count)
    for round_info in rounds:
        if stop_real_dialogue_if_requested():
            return
        round_num = round_info["round"]
        emit("round_start", {"round": round_num, "max_round": len(rounds)})
        try:
            codex_text = _run_codex_dialogue_turn(topic, transcript, round_num, len(rounds))
            if stop_real_dialogue_if_requested():
                return
            transcript.append({"speaker": "ChatGPT", "text": codex_text})
            append_session_message("Codex", "ChatGPT", codex_text)
            emit("step_done", {"step": "plan", "actor": "Codex", "summary": f"ChatGPT 第 {round_num} 轮说完"})
        except DialogueStopped:
            stop_real_dialogue_if_requested()
            return
        except Exception as exc:
            emit("token", {"model": "System", "role": "❌错误", "text": str(exc), "is_thinking": False})
            finish_real_dialogue("真实对话异常结束")
            return

        try:
            reasonix_text = _run_reasonix_dialogue_turn(topic, transcript, round_num, len(rounds))
            if stop_real_dialogue_if_requested():
                return
            transcript.append({"speaker": "DeepSeek", "text": reasonix_text})
            append_session_message("Reasonix", "DeepSeek", reasonix_text)
            emit("step_done", {"step": "build", "actor": "Reasonix", "summary": f"DeepSeek 第 {round_num} 轮接完"})
        except DialogueStopped:
            stop_real_dialogue_if_requested()
            return
        except Exception as exc:
            emit("token", {"model": "System", "role": "❌错误", "text": str(exc), "is_thinking": False})
            finish_real_dialogue("真实对话异常结束")
            return

    append_session_event("real_dialogue_done", f"真实模型对话完成：{topic}")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    finish_real_dialogue("真实对话完成")


def build_dialogue_round_prompts(topic: str, rounds: int = 3):
    return [{"round": i, "topic": topic} for i in range(1, rounds + 1)]


def build_dev_rehearsal_phases():
    return [
        {
            "round": 1,
            "title": "最小记事本",
            "task": (
                "开发演练第 1 轮：创建一个简易命令行记事本 mini_notes.py，"
                "只支持 add <text> 和 list。数据保存到当前工作目录的 notes.json。"
                "请同时添加 tests/test_mini_notes.py，覆盖 add 和 list。"
                "实现要简单、通用、中文错误提示，不要做额外功能。"
            ),
        },
        {
            "round": 2,
            "title": "删除笔记",
            "task": (
                "开发演练第 2 轮：在现有 mini_notes.py 基础上增加 delete <id>。"
                "删除成功后 list 不再显示该笔记；删除不存在的 id 时输出中文错误提示且不崩溃。"
                "补充或更新 tests/test_mini_notes.py，只验证 delete 的必要行为。"
            ),
        },
        {
            "round": 3,
            "title": "搜索和摘要",
            "task": (
                "开发演练第 3 轮：在现有 mini_notes.py 基础上增加 search <keyword> 和 summary。"
                "search 返回包含关键词的笔记；summary 输出笔记数量和最近一条笔记。"
                "补充 tests/test_mini_notes.py，覆盖 search 和 summary。"
            ),
        },
    ]


def _finish_dev_rehearsal(summary: str):
    emit("step_done", {
        "step": "pipeline_complete",
        "actor": "System",
        "summary": summary,
    })


def _run_dev_rehearsal(rounds_count: int = 3):
    load_workspace_root()
    apply_model_config_env()
    apply_auth_env()

    phases = build_dev_rehearsal_phases()[:rounds_count]
    append_session_event("dev_rehearsal", f"开发演练开始：{len(phases)} 轮")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    emit("token", {
        "model": "System",
        "role": "说明",
        "text": "开发演练会复用现有 Web 流：Codex 规划，Reasonix 施工并自检，Codex 验收；通过后才进入下一轮。\n",
        "is_thinking": False,
    })

    for phase in phases:
        if _stop_requested.is_set():
            emit("stopped", {"reason": "user_graceful"})
            _finish_dev_rehearsal("开发演练已停止")
            return

        round_num = phase["round"]
        emit("round_start", {"round": round_num, "max_round": len(phases)})
        append_session_event("dev_rehearsal_phase", f"第 {round_num} 轮：{phase['title']}")

        packet = f"第 {round_num} 轮任务包：{phase['title']}\n{phase['task']}\n"
        emit_text_stream("Codex", "任务包", packet, delay=0.0)
        append_session_message("Codex", "任务包", packet)

        init_task(phase["task"], max_round=1)

        if _pipeline_stage_plan(round_num, len(phases)):
            return
        if _pipeline_stage_build(round_num, len(phases)):
            return
        _pipeline_stage_test(round_num, len(phases))

        outcome = _pipeline_stage_review(round_num, len(phases))
        if outcome == PIPELINE_APPROVED:
            emit("step_done", {
                "step": "phase_approved",
                "actor": "Codex",
                "summary": f"第 {round_num} 轮验收通过，进入下一轮",
            })
            continue
        if outcome == PIPELINE_MAX_ROUND:
            _finish_dev_rehearsal(f"第 {round_num} 轮达到最大轮次，演练停止")
            return

        _finish_dev_rehearsal(f"第 {round_num} 轮未通过验收，演练停止，等待修复")
        return

    append_session_event("dev_rehearsal_done", f"开发演练完成：{len(phases)} 轮")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    _finish_dev_rehearsal("开发演练完成，三轮增量均已通过")


def _format_transcript(transcript: list[dict]) -> str:
    if not transcript:
        return "（尚无前文）"
    return "\n".join(f"{item['speaker']}：{item['text']}" for item in transcript[-4:])


def filter_codex_dialogue_lines(lines: list[str]) -> list[str]:
    blocked_fragments = [
        "hook:",
        "Stop hook:",
        "tokens used",
        "succeeded in",
        "Failed",
    ]
    clean = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(fragment.lower() in stripped.lower() for fragment in blocked_fragments):
            continue
        clean.append(stripped)
    return clean


def _run_codex_dialogue_turn(
    topic: str,
    transcript: list[dict],
    round_num: int,
    max_rounds: int,
) -> str:
    prompt = f"""
请只输出一小段相声里 ChatGPT 这一方的台词，不要调用工具，不要修改文件。
主题：{topic}
当前第 {round_num}/{max_rounds} 轮。
前文：
{_format_transcript(transcript)}
要求：中文，幽默，2 到 4 句。不要写“甲：”前缀，不要自称甲。每段自然加入 1 到 2 个 emo 表情图案或颜文字，例如 🤣、(。﹏。*)、(╯°□°）╯︵ ┻━┻，不要刷屏。
"""
    captured = []
    worker = get_codex_app_worker()

    def on_token(token: str):
        if _stop_requested.is_set():
            raise DialogueStopped("真实对话已停止")
        captured.append(token)
        emit_text_stream("Codex", "ChatGPT", token, delay=0.0)

    text = worker.ask(prompt, on_token=on_token).strip()
    if not text:
        raise RuntimeError("Codex 没有返回相声台词，可能是连接超时。")
    return text


def _run_reasonix_dialogue_turn(topic: str, transcript: list[dict], round_num: int, max_rounds: int) -> str:
    import os as _os
    from openai import OpenAI

    apply_model_config_env()
    api_key = _os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")

    config = load_model_config()
    base_url = _os.environ.get("DEEPSEEK_BASE_URL", config["deepseek_base_url"])
    model = config.get("deepseek_model", "deepseek-chat")
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你在说一段中文相声，只输出 DeepSeek 这一方台词，不要写代码，不要改文件。可以自然加入少量 emo 表情图案或颜文字，让画面更生动。"},
            {"role": "user", "content": f"主题：{topic}\n当前第 {round_num}/{max_rounds} 轮。\n前文：\n{_format_transcript(transcript)}\n请接 2 到 4 句，幽默自然。不要写“乙：”前缀，不要自称乙。每段自然加入 1 到 2 个 emo 表情图案或颜文字，例如 🤣、(。﹏。*)、(╯°□°）╯︵ ┻━┻，不要刷屏。"},
        ],
        temperature=0.8,
        max_tokens=1200,
        stream=True,
    )

    captured = []
    for chunk in response:
        if _stop_requested.is_set():
            raise DialogueStopped("真实对话已停止")
        token = chunk.choices[0].delta.content
        if not token:
            continue
        captured.append(token)
        emit_text_stream("Reasonix", "DeepSeek", token, delay=0.005)
    text = "".join(captured).strip()
    if not text:
        raise RuntimeError("DeepSeek 没有返回相声台词。")
    return text


# ── 流水线 ──────────────────────────────────────────────

def emit_stage(stage: str, status: str, detail: str = "", **extra):
    payload = {
        "kind": "pipeline_stage",
        "label": "Pipeline",
        "title": f"{stage}: {status}",
        "detail": detail,
        "workspace": str(load_workspace_root()),
        "conversation_id": "",
        "ts": time.time(),
        **extra,
    }
    emit_qa_external(payload)
    emit("status", {"text": f"{stage}: {status}" + (f" - {detail}" if detail else "")})


def run_with_timeout(label: str, func, timeout: int = CODEX_STAGE_TIMEOUT_SECONDS):
    result = {}

    def target():
        try:
            result["value"] = func()
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        close_codex_app_worker()
        raise TimeoutError(f"{label} timed out after {timeout} seconds")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def prepare_direct_reasonix_packet(task_text: str, max_round: int):
    plan_md = (
        "# Direct Reasonix Packet Mode\n\n"
        "Codex received a pre-written Reasonix implementation packet. "
        "Skip additional planning and hand this packet directly to Reasonix.\n\n"
        "Reasonix must follow the packet exactly, produce concise evidence, "
        "and write `.agent/build_report.md` before returning."
    )
    acceptance = {
        "mode": "direct_reasonix_packet",
        "expected_status": "READY_FOR_CODEX_REVIEW",
        "requirements": [
            "Reasonix must follow the user-provided packet.",
            "Reasonix must write .agent/build_report.md.",
            "Reasonix must provide evidence paths and self-check results when requested by the packet.",
        ],
    }
    from tools.ai_flow import write_text as ai_write_text, save_state

    ai_write_text(".agent/codex_plan.md", plan_md)
    ai_write_text(".agent/codex_plan_result.md", f"CODEX_PLAN_MD\n{plan_md}\n\nACCEPTANCE_JSON\n{json.dumps(acceptance, ensure_ascii=False, indent=2)}")
    ai_write_text(".agent/acceptance.json", json.dumps(acceptance, ensure_ascii=False, indent=2))
    state = load_state()
    state.update({
        "round": 1,
        "max_round": max_round,
        "status": "WAIT_REASONIX_BUILD",
        "approved": False,
        "last_actor": "codex",
        "next_actor": "reasonix",
        "direct_reasonix": True,
    })
    save_state(state)


def _pipeline_stage_plan(round_num: int, max_round: int):
    """Codex 规划阶段。返回 PIPELINE_ERROR/PIPELINE_STOPPED，或 None 表示正常完成。"""
    emit_stage("Codex 规划", "开始", f"Round {round_num}/{max_round}")
    append_session_event("codex_plan", f"Round {round_num}: Codex 规划")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    try:
        on_token, captured = make_recording_token_sink()
        run_with_timeout(
            "Codex plan",
            lambda: codex_plan(on_token=on_token, worker=get_codex_app_worker()),
        )
        save_captured_messages(captured)
    except Exception as exc:
        emit_stage("Codex 规划", "失败", str(exc))
        emit("token", {"model": "System", "role": "❌错误", "text": str(exc), "is_thinking": False})
        return PIPELINE_ERROR

    if _stop_requested.is_set():
        emit_stage("Codex 规划", "已停止")
        emit("stopped", {"reason": "user_graceful"})
        return PIPELINE_STOPPED
    append_session_event("codex_plan_done", f"Round {round_num}: Codex 规划完成")
    emit_stage("Codex 规划", "完成", f"Round {round_num}/{max_round}")
    emit("step_done", {"step": "plan", "actor": "Codex", "summary": "规划完成"})
    return None


def _pipeline_stage_build(round_num: int, max_round: int):
    """Reasonix 施工阶段。返回 PIPELINE_ERROR/PIPELINE_STOPPED，或 None 表示正常完成。"""
    emit_stage("Reasonix 施工", "开始", f"Round {round_num}/{max_round}")
    append_session_event("reasonix_build", f"Round {round_num}: Reasonix 施工")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    worker_session = WorkerSession("Reasonix", f"Round {round_num} build", load_workspace_root())
    try:
        worker_session.start()
        progress_lines = []

        def on_reasonix_progress(text: str):
            if not text:
                return
            progress_lines.append(text)
            worker_session.progress_event(text)

        emit("status", {"text": "Reasonix 正在执行：页面显示精简过程，完整日志会落盘。"})
        reasonix_build(on_token=None, on_progress=on_reasonix_progress)
        if progress_lines:
            progress_text = "\n".join(progress_lines)
            if len(progress_text) > 6000:
                progress_text = progress_text[:6000] + "\n[过程过长，已截断；完整日志见 .agent/logs/reasonix_stdout.log]"
            append_session_message("Reasonix", "工作过程", progress_text)
        reasonix_summary = summarize_reasonix_result()
        emit("token", {
            "model": "Reasonix",
            "role": "DeepSeek 摘要",
            "text": reasonix_summary,
            "is_thinking": False,
        })
        append_session_message("Reasonix", "DeepSeek 摘要", reasonix_summary)
        worker_session.complete("Reasonix build completed")
    except Exception as exc:
        worker_session.fail(exc)
        emit_stage("Reasonix 施工", "失败", str(exc))
        emit("token", {"model": "System", "role": "❌错误", "text": str(exc), "is_thinking": False})
        return PIPELINE_ERROR

    if _stop_requested.is_set():
        emit_stage("Reasonix 施工", "已停止")
        emit("stopped", {"reason": "user_graceful"})
        return PIPELINE_STOPPED

    append_session_event("reasonix_build_done", f"Round {round_num}: Reasonix 施工完成")
    emit_stage("Reasonix 施工", "完成", f"Round {round_num}/{max_round}")
    _emit_file_updates()
    emit("step_done", {"step": "build", "actor": "Reasonix", "summary": "施工完成"})
    return None


def _review_failure_signature(state: dict | None = None) -> str:
    """Return a stable-ish signature for the current Codex review failure."""
    state = state if state is not None else load_state()
    next_fix = read_text(".agent/next_fix.md").strip()
    source = next_fix or str(state.get("status") or "")
    normalized = re.sub(r"\s+", " ", source).strip()
    return normalized[:2000] or "unknown-review-failure"


def _make_codex_fallback_prompt(same_failure_count: int) -> str:
    task = read_text(".agent/task.md").strip()
    plan = read_text(".agent/codex_plan.md").strip()
    acceptance = read_text(".agent/acceptance.json").strip()
    next_fix = read_text(".agent/next_fix.md").strip()
    build_report = read_text(".agent/build_report.md").strip()
    test_log = read_text(".agent/test.log").strip()
    return f"""# Codex Temporary Fallback Implementation

Reasonix has failed to resolve the same review finding {same_failure_count} consecutive times.
For this one task only, Codex is allowed to edit implementation code directly.
After this fallback attempt, the pipeline must return to the normal Reasonix worker path if more work is needed.

## Rules

- Implement only the smallest changes required by the current Codex review finding.
- Do not broaden scope, redesign unrelated modules, or rewrite unrelated files.
- Do not use Reasonix or create a generic subagent.
- Do not commit, push, deploy, or read secrets.
- Run the most relevant tests you can run locally.
- Write a concise `.agent/build_report.md` before finishing.

## Original Task

{task}

## Codex Plan

{plan}

## Acceptance Criteria

{acceptance}

## Current Failed Review / Required Fix

{next_fix or "No next_fix.md was written. Inspect the current task, build report, tests, and workspace diff, then make the smallest safe fix."}

## Previous Worker Build Report

{build_report or "No build report found."}

## Test Log

{test_log[:12000] if test_log else "No test log found."}

## Required Final Evidence

Update `.agent/build_report.md` with:

- status: READY_FOR_CODEX_REVIEW, NEEDS_FIX, BLOCKED, or NEEDS_CLARIFICATION
- changed files
- commands/tests run
- artifact/evidence paths
- known risks
- short summary
"""


def _pipeline_stage_codex_fallback_build(round_num: int, max_round: int, same_failure_count: int):
    """Codex 临时兜底实现。只用于同一 review failure 连续 3 次未被 Reasonix 修复后的一次 task。"""
    detail = f"Reasonix 同一问题已连续失败 {same_failure_count} 次；Codex 临时接手本 task 一次"
    emit_stage("Codex 兜底实现", "开始", detail)
    append_session_event("codex_fallback_build", f"Round {round_num}: Codex 兜底实现")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    try:
        captured = []

        def on_token(token: str):
            emit_pipeline_token("Codex", "兜底实现", token, False)
            if token:
                captured.append({
                    "model": "Codex",
                    "role": "兜底实现",
                    "text": token,
                    "is_thinking": False,
                })

        prompt = _make_codex_fallback_prompt(same_failure_count)
        result_text = run_with_timeout(
            "Codex fallback build",
            lambda: get_codex_app_worker().ask(prompt, on_token=on_token),
            timeout=CODEX_FALLBACK_TIMEOUT_SECONDS,
        )
        write_text(".agent/logs/codex_fallback_build.log", result_text or "")
        save_captured_messages(captured)
        state = load_state()
        state["last_actor"] = "codex"
        state["next_actor"] = "orchestrator"
        state["codex_fallback_used"] = True
        from tools.ai_flow import save_state
        save_state(state)
    except Exception as exc:
        emit_stage("Codex 兜底实现", "失败", str(exc))
        emit("token", {"model": "System", "role": "❌错误", "text": str(exc), "is_thinking": False})
        return PIPELINE_ERROR

    if _stop_requested.is_set():
        emit_stage("Codex 兜底实现", "已停止")
        emit("stopped", {"reason": "user_graceful"})
        return PIPELINE_STOPPED

    append_session_event("codex_fallback_build_done", f"Round {round_num}: Codex 兜底实现完成")
    emit_stage("Codex 兜底实现", "完成", f"Round {round_num}/{max_round}")
    _emit_file_updates()
    emit("step_done", {"step": "codex_fallback_build", "actor": "Codex", "summary": "Codex 兜底实现完成，后续恢复 Reasonix"})
    return None


def _pipeline_stage_test(round_num: int, max_round: int):
    """测试阶段。不主动终止（异常被吞掉），始终返回 None。"""
    emit_stage("测试", "开始", f"Round {round_num}/{max_round}")
    append_session_event("test", f"Round {round_num}: 测试")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    try:
        run_tests()
    except Exception:
        pass
    emit_stage("测试", "完成", f"Round {round_num}/{max_round}")
    emit("step_done", {"step": "test", "actor": "System", "summary": "测试完成"})
    return None


def _pipeline_stage_review(round_num: int, max_round: int):
    """Codex 验收阶段。返回终止性控制状态（PIPELINE_APPROVED/PIPELINE_MAX_ROUND）或 None 表示继续下一轮。"""
    emit_stage("Codex 验收", "开始", f"Round {round_num}/{max_round}")
    append_session_event("codex_review", f"Round {round_num}: Codex 验收")
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    try:
        on_token, captured = make_recording_token_sink()
        run_with_timeout(
            "Codex review",
            lambda: codex_review(on_token=on_token, worker=get_codex_app_worker()),
        )
        save_captured_messages(captured)
    except Exception as exc:
        emit_stage("Codex 验收", "失败", str(exc))

    state = load_state()
    emit("review", {
        "status": state.get("status", "UNKNOWN"),
        "risk_level": "low",
        "blocking_issues": [],
        "non_blocking_issues": [],
        "summary": state.get("status", ""),
    })
    append_session_event("review_done", f"Round {round_num}: {state.get('status', 'UNKNOWN')}")
    emit_stage("Codex 验收", "完成", state.get("status", "UNKNOWN"))
    emit("context", {"metrics": context_metrics(), "session": load_session()})
    return _classify_review_outcome(state)


def _classify_review_outcome(state: dict | None = None) -> str:
    """根据 ai_flow 写入的 state 分类本轮验收后的控制状态。

    权威源是 state['approved'] 和 state['status']，不再用 server 的循环变量
    round_num 做二次判断（消除双重计数器漂移）。CHANGES_REQUESTED / WAIT_REASONIX_FIX
    等非终止状态统一归为 PIPELINE_RUNNING（继续下一轮）。
    """
    state = state if state is not None else load_state()
    if state.get("approved"):
        return PIPELINE_APPROVED
    if state.get("status") in ("MAX_ROUND_REACHED", "MAX_ROUND_RECOMMENDED_STOP"):
        return PIPELINE_MAX_ROUND
    return PIPELINE_RUNNING


def _finish_pipeline(outcome: str):
    """pipeline 收尾：按控制状态集中发送原本散落在各分支的终止事件。

    STOPPED / ERROR 不发 pipeline_completed（保持原行为：这两条路径原本直接 return，
    不发送完成事件）。RUNNING 仅在循环正常结束（所有轮次跑完仍未 approved）时触发。
    """
    if outcome == PIPELINE_APPROVED:
        emit_agent_event("pipeline_completed", "System", "Pipeline approved")
        emit("step_done", {"step": "pipeline_complete", "actor": "Codex", "summary": "✅ 任务验收通过"})
    elif outcome == PIPELINE_MAX_ROUND:
        state = load_state()
        emit_agent_event("pipeline_completed", "System", state.get("status", ""))
        emit("step_done", {"step": "pipeline_complete", "actor": "Codex", "summary": "⚠️ 达到最大轮次"})
    elif outcome == PIPELINE_RUNNING:
        emit_agent_event("pipeline_completed", "System", "All rounds completed")
        emit("step_done", {"step": "pipeline_complete", "actor": "System", "summary": "已完成所有轮次"})
    # PIPELINE_STOPPED / PIPELINE_ERROR：不发完成事件，直接返回（保持原行为）


def _run_pipeline(max_round: int, direct_reasonix: bool = False):
    global _current_proc
    load_workspace_root()
    apply_model_config_env()
    apply_auth_env()

    emit_agent_event(
        "pipeline_started",
        "System",
        "Development pipeline started",
        max_round=max_round,
        direct_reasonix=direct_reasonix,
    )
    same_failure_count = 0
    last_failure_signature = ""
    for round_num in range(1, max_round + 1):
        if _stop_requested.is_set():
            emit_agent_event("pipeline_stopped", "System", "Pipeline stopped before round")
            return  # PIPELINE_STOPPED

        emit("round_start", {"round": round_num, "max_round": max_round})

        if direct_reasonix and round_num == 1:
            emit_stage("Codex 规划", "跳过", "Direct Reasonix packet mode")
            append_session_event("codex_plan_skipped", "Round 1: Direct Reasonix packet mode")
        elif _pipeline_stage_plan(round_num, max_round):
            return
        if _pipeline_stage_build(round_num, max_round): return
        _pipeline_stage_test(round_num, max_round)

        outcome = _pipeline_stage_review(round_num, max_round)
        if outcome == PIPELINE_APPROVED:
            _finish_pipeline(outcome)
            return
        state = load_state()
        failure_signature = _review_failure_signature(state)
        if failure_signature == last_failure_signature:
            same_failure_count += 1
        else:
            same_failure_count = 1
            last_failure_signature = failure_signature
        if same_failure_count >= CODEX_FALLBACK_FAILURE_THRESHOLD:
            emit_agent_event(
                "codex_fallback_triggered",
                "Codex",
                "Reasonix failed the same issue three times; Codex will fix this task once.",
                failure_count=same_failure_count,
            )
            if _pipeline_stage_codex_fallback_build(round_num, max_round, same_failure_count):
                return
            same_failure_count = 0
            last_failure_signature = ""
            _pipeline_stage_test(round_num, max_round)
            outcome = _pipeline_stage_review(round_num, max_round)
            if outcome in (PIPELINE_APPROVED, PIPELINE_MAX_ROUND):
                _finish_pipeline(outcome)
                return
        elif outcome == PIPELINE_MAX_ROUND:
            _finish_pipeline(outcome)
            return

    # 所有轮次跑完仍未 approved → 正常结束
    _finish_pipeline(PIPELINE_RUNNING)


def _emit_file_updates():
    tree = _build_file_tree()
    emit("file_update", {"tree": tree})


def _build_file_tree():
    workspace = load_workspace_root()
    tree = {"name": workspace.name, "type": "dir", "path": "", "children": []}

    ignore = {".git", ".reasonix", "__pycache__", ".superpowers",
              ".agent/events", ".agent/logs", ".agent/outbox"}

    def walk(path: Path, node: dict):
        try:
            for p in sorted(path.iterdir()):
                rel = str(p.relative_to(workspace)).replace("\\", "/")
                if any(rel.startswith(i) for i in ignore):
                    continue
                if p.name.startswith(".") and p.name not in (".agent", ".gitignore"):
                    continue
                if p.is_dir():
                    child = {"name": p.name, "type": "dir", "path": rel, "children": []}
                    node["children"].append(child)
                    walk(p, child)
                else:
                    node["children"].append({
                        "name": p.name, "type": "file", "path": rel,
                        "size": p.stat().st_size
                    })
        except PermissionError:
            pass

    walk(workspace, tree)
    return tree


# ── main ────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_dirs()
    watch_state = load_qa_watch_state()
    watch_workspace = watch_state.get("workspace")
    if watch_workspace:
        watch_path = Path(watch_workspace)
        if watch_path.exists() and watch_path.is_dir():
            start_qa_watch_thread(watch_path.resolve(), float(watch_state.get("interval") or 5.0))
    host = get_host()
    port = get_port()
    print(f"Handoff Lab monitor -> http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
