#!/usr/bin/env python3
"""Create a Handoff Lab implementation packet from JSON.

Input is read from a JSON file path argument or stdin. Use --example to print a
minimal JSON input example.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


EXAMPLE = {
    "task": "Add delete buttons to history conversations and persist deletion.",
    "codex_interpretation": "The UI should let users remove old history entries without affecting active runs.",
    "repository": "C:/path/to/project",
    "working_directory": "C:/path/to/project",
    "max_rounds": 3,
    "context_summary": [
        "History entries are rendered in the left sidebar.",
        "Conversation metadata is persisted by the local server.",
    ],
    "global_constraints": [
        "Do not change unrelated routing.",
        "Do not delete user files outside the conversation store.",
    ],
    "tasklets": [
        {
            "id": "T1",
            "goal": "Add a delete affordance for each history item.",
            "allowed_files": ["static/ui.js", "static/style.css", "server.py"],
            "forbidden_files": [".env"],
            "steps": [
                "Locate the history rendering code.",
                "Add a compact delete button with confirmation.",
                "Call a server endpoint to delete the selected conversation.",
            ],
            "tests": ["Run the existing UI/server smoke test if present."],
            "acceptance": [
                "A deleted item disappears from the sidebar.",
                "The active conversation is not cleared unless it was deleted.",
            ],
            "evidence": ["Changed file list", "Test log", "Manual verification note"],
        }
    ],
    "commands": ["python -m pytest"],
    "known_risks": ["No browser automation may be configured locally."],
}


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def bullet_lines(items: Any, indent: str = "- ") -> str:
    rows = as_list(items)
    if not rows:
        return f"{indent}None"
    return "\n".join(f"{indent}{item}" for item in rows)


def require(data: dict[str, Any], key: str) -> None:
    if key not in data or data[key] in ("", None, []):
        raise ValueError(f"Missing required field: {key}")


def render_tasklet(tasklet: dict[str, Any], index: int) -> str:
    tasklet_id = tasklet.get("id") or f"T{index}"
    goal = tasklet.get("goal") or "No goal provided"
    return f"""### Tasklet {tasklet_id}: {goal}

- Goal: {goal}
- Allowed files:
{bullet_lines(tasklet.get("allowed_files"), "  - ")}
- Forbidden files:
{bullet_lines(tasklet.get("forbidden_files"), "  - ")}
- Required steps:
{bullet_lines(tasklet.get("steps"), "  - ")}
- Required tests:
{bullet_lines(tasklet.get("tests"), "  - ")}
- Acceptance checks:
{bullet_lines(tasklet.get("acceptance"), "  - ")}
- Evidence to return:
{bullet_lines(tasklet.get("evidence"), "  - ")}
- Stop/block conditions:
{bullet_lines(tasklet.get("blockers"), "  - ")}
"""


def render_packet(data: dict[str, Any]) -> str:
    require(data, "task")
    require(data, "tasklets")

    tasklets = data.get("tasklets")
    if not isinstance(tasklets, list) or not tasklets:
        raise ValueError("tasklets must be a non-empty list")

    rendered_tasklets = "\n".join(
        render_tasklet(tasklet, idx + 1) for idx, tasklet in enumerate(tasklets)
    )

    return f"""# HANDOFF_LAB_IMPLEMENTATION_PACKET

## Header

- packet_id: {data.get("packet_id", "manual")}
- created_by: Codex
- target_worker: Implementation worker
- expected_status: READY_FOR_CODEX_REVIEW
- repository: {data.get("repository", "unspecified")}
- working_directory: {data.get("working_directory", data.get("repository", "unspecified"))}
- max_rounds: {data.get("max_rounds", 3)}

## Original User Request

{data["task"]}

## Codex Interpretation

{data.get("codex_interpretation", "Implement exactly as specified by the tasklets below.")}

## Context Pack

{bullet_lines(data.get("context_summary"))}

## Global Constraints

{bullet_lines(data.get("global_constraints"))}

## Tasklets

{rendered_tasklets}
## Commands To Run

{bullet_lines(data.get("commands"))}

## Known Risks

{bullet_lines(data.get("known_risks"))}

## Worker Self-QA Gate

Before returning to Codex, verify:

- [ ] Every tasklet is completed or explicitly blocked.
- [ ] No forbidden files were changed.
- [ ] No sample-specific hardcoding was added.
- [ ] Required tests were run or the blocker is documented.
- [ ] Generated artifacts exist at the claimed paths.
- [ ] Build report is concise and evidence-oriented.

Return this JSON:

```json
{{
  "status": "READY_FOR_CODEX_REVIEW",
  "scores": {{
    "implementation_completeness": 0,
    "test_coverage": 0,
    "evidence_quality": 0,
    "risk_control": 0,
    "instruction_following": 0
  }},
  "changed_files": [],
  "generated_artifacts": [],
  "test_log_paths": [],
  "evidence_paths": [],
  "known_risks": [],
  "summary": ""
}}
```

If any score is below 4, required evidence is missing, or any required test fails, do not return `READY_FOR_CODEX_REVIEW`.
"""


def load_input(path: str | None) -> dict[str, Any]:
    raw = sys.stdin.read() if not path or path == "-" else open(path, "r", encoding="utf-8").read()
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Handoff Lab implementation packet from JSON.")
    parser.add_argument("input", nargs="?", help="JSON input file. Reads stdin when omitted or '-'.")
    parser.add_argument("--example", action="store_true", help="Print example JSON input.")
    args = parser.parse_args()

    if args.example:
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0

    try:
        data = load_input(args.input)
        print(render_packet(data))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
