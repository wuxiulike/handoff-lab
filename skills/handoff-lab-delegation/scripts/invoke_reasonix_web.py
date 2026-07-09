#!/usr/bin/env python3
"""Invoke the local Handoff Lab Web bridge.

This is intentionally small and explicit: it does not implement code itself and
does not spawn generic Codex subagents. It only submits a task packet to the
local Web bridge, which owns the real implementation worker execution path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


class BridgeHttpError(RuntimeError):
    def __init__(self, path: str, status: int, body: str):
        self.path = path
        self.status = status
        self.body = body
        try:
            self.payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            self.payload = {}
        super().__init__(f"{path} failed: HTTP {status}: {body}")


def get_json(base_url: str, path: str, timeout: int = 5) -> dict:
    req = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return {}
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


def discover_base_url(workspace: str) -> str:
    configured = os.environ.get("HANDOFF_LAB_URL", "").strip()
    if configured:
        return configured

    workspace_path = str(Path(workspace).expanduser().resolve()).lower()
    candidates = [
        "http://127.0.0.1:51514",
        "http://127.0.0.1:51515",
        "http://127.0.0.1:51516",
    ]
    healthy = []
    for candidate in candidates:
        health = get_json(candidate, "/api/health", timeout=2)
        if not health.get("ok"):
            continue
        healthy.append(candidate)
        service_workspace = str(health.get("workspace") or "").lower()
        if service_workspace == workspace_path:
            return candidate
    if len(healthy) == 1:
        return healthy[0]
    if healthy:
        joined = ", ".join(healthy)
        raise RuntimeError(
            "Multiple Handoff Lab services are running, but none reports the "
            f"target workspace {workspace_path}. Set HANDOFF_LAB_URL or pass "
            f"--base-url explicitly. Healthy candidates: {joined}"
        )
    return candidates[0]


def post_json(base_url: str, path: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise BridgeHttpError(path, exc.code, body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{path} failed: {exc.reason}") from exc
    return json.loads(raw or "{}")


def wait_for_authorized_start(base_url: str, timeout_seconds: int, poll_interval: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_state = {}
    while time.monotonic() < deadline:
        auth = get_json(base_url, "/api/auth", timeout=5)
        pending = get_json(base_url, "/api/auth/pending-start", timeout=5)
        if pending.get("pending") and auth.get("mode") in {"allow", "yolo"}:
            decision = auth["mode"]
            return post_json(base_url, "/api/auth/pending-start", {"decision": decision})
        if pending.get("pending"):
            time.sleep(poll_interval)
            continue

        state = get_json(base_url, "/api/state", timeout=5)
        if state.get("running"):
            return {"status": "started_after_authorization", "state": state}
        if state.get("terminal") and state.get("status") not in {"UNKNOWN", None, ""}:
            return {"status": "completed_after_authorization", "state": state}
        if not pending.get("pending") and state:
            last_state = state
        time.sleep(poll_interval)

    raise TimeoutError(
        "authorization_required: timed out waiting for /qa-viewer approval. "
        "Open the Handoff Lab QA Viewer and approve the pending worker start."
        f" Last state: {json.dumps(last_state, ensure_ascii=False)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a task packet to the local Handoff Lab Web bridge.")
    parser.add_argument(
        "--base-url",
        default="",
        help="Handoff Lab Web bridge URL. Defaults to HANDOFF_LAB_URL or local auto-discovery.",
    )
    parser.add_argument("--workspace", required=True, help="Target workspace for the implementation.")
    parser.add_argument("--task-file", help="Markdown packet path to submit.")
    parser.add_argument("--task", help="Inline task text. Prefer --task-file for real work.")
    parser.add_argument("--rounds", type=int, default=3, help="Maximum Codex/Reasonix review rounds.")
    parser.add_argument("--auth-timeout", type=int, default=600, help="Seconds to wait for /qa-viewer authorization.")
    parser.add_argument("--no-watch", action="store_true", help="Do not start qa-viewer workspace monitoring.")
    parser.add_argument(
        "--direct-reasonix",
        action="store_true",
        help="Skip Codex planning and hand the packet directly to Reasonix.",
    )
    args = parser.parse_args()

    workspace = str(Path(args.workspace).expanduser())
    if args.task_file:
        task = Path(args.task_file).expanduser().read_text(encoding="utf-8")
    else:
        task = args.task or ""
    if not task.strip():
        raise SystemExit("missing task: pass --task-file or --task")

    base_url = args.base_url.strip() or discover_base_url(workspace)
    workspace_result = post_json(base_url, "/api/workspace", {"path": workspace})
    result = {
        "workspace": {
            "path": workspace_result.get("path", workspace),
            "tree_root": (workspace_result.get("tree") or {}).get("name", ""),
        },
    }
    if not args.no_watch:
        result["watch"] = post_json(base_url, "/api/qa-watch", {"workspace": workspace, "interval": 5})
    start_payload = {
        "task": task,
        "max_round": args.rounds,
        "direct_reasonix": args.direct_reasonix,
    }
    try:
        result["start"] = post_json(base_url, "/api/start", start_payload)
    except BridgeHttpError as exc:
        if exc.status != 403 or exc.payload.get("error") != "authorization_required":
            raise
        print(
            "authorization_required: waiting for approval in Handoff Lab /qa-viewer...",
            file=sys.stderr,
            flush=True,
        )
        result["authorization"] = exc.payload
        result["start"] = wait_for_authorized_start(base_url, args.auth_timeout)
    result["base_url"] = base_url

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BLOCKED: Reasonix Web bridge invocation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
