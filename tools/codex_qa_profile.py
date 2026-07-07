"""Codex QA review profile borrowed from the standalone codex-qa workflow.

The profile is intentionally transport-neutral: Handoff Lab keeps ownership of
worker orchestration, while these helpers only shape Codex review output.
"""

QA_GUIDANCE_MARKDOWN_SECTIONS = [
    "Conclusion",
    "What I Verified",
    "Passed Items",
    "Failed Items",
    "Next Development Guidance",
    "Required Evidence For Next Round",
    "Boundaries And Final Judgment",
]


def review_profile_prompt() -> str:
    sections = "\n".join(f"{idx}. {name}" for idx, name in enumerate(QA_GUIDANCE_MARKDOWN_SECTIONS, 1))
    return f"""
# Codex QA Review Profile

Use the Handoff Lab review package as hard evidence. In addition to the compact
JSON fields, write a concise `guidance_markdown` field when the review is not
approved, or when the next worker round would benefit from precise instructions.

`guidance_markdown` must follow this 7-section structure:

{sections}

Guidance rules:

- Codex is read-only during review. Do not edit implementation code.
- Verify claims against actual files, diffs, test logs, generated artifacts,
  screenshots, reports, or command output.
- Write concrete next tasks: target files, required changes, required tests,
  expected artifacts, and pass/fail criteria.
- Keep fix rounds narrow. Do not re-plan from scratch unless the previous plan
  is demonstrably wrong.
- If evidence is missing, ask for the exact missing evidence path or command log.
""".strip()


def review_schema_extension() -> dict:
    """Optional fields and relaxed issue item shape for Codex review JSON."""
    issue_item = {
        "anyOf": [
            {"type": "string"},
            {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "id": {"type": "string"},
                    "issue": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        ]
    }
    return {
        "blocking_issues": {"type": "array", "items": issue_item},
        "non_blocking_issues": {"type": "array", "items": issue_item},
        "fix_instructions": {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object", "additionalProperties": True},
                ]
            },
        },
        "guidance_markdown": {
            "type": "string",
            "description": "Optional 7-section Markdown guidance for the next worker round.",
        },
    }


def format_next_fix_guidance(review: dict) -> str:
    """Build the next worker instruction text from review JSON."""
    guidance = (review.get("guidance_markdown") or "").strip()
    instructions = review.get("fix_instructions") or []
    lines = []
    if guidance:
        lines.append("# Codex QA Guidance")
        lines.append("")
        lines.append(guidance)
    if instructions:
        if lines:
            lines.append("")
        lines.append("# Compact Fix Instructions")
        for item in instructions:
            if isinstance(item, str):
                lines.append(f"- {item}")
            else:
                issue = item.get("issue") or item.get("title") or item.get("id") or "instruction"
                evidence = item.get("evidence")
                lines.append(f"- {issue}")
                if evidence:
                    lines.append(f"  Evidence: {evidence}")
    return "\n".join(lines).strip()
