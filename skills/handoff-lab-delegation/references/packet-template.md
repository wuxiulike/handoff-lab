# Handoff Lab Implementation Packet Template

Use this template when Codex delegates implementation to a Handoff Lab worker. The packet should be specific, executable, and evidence-oriented.

```markdown
# HANDOFF_LAB_IMPLEMENTATION_PACKET

## Header

- packet_id:
- created_by: Codex
- target_worker:
- transport: local_web_bridge | direct_worker_cli | project_worker_adapter
- transport_command_or_endpoint:
- expected_status: READY_FOR_CODEX_REVIEW | NEEDS_FIX | BLOCKED | NEEDS_CLARIFICATION
- repository:
- working_directory:
- max_rounds:

## Original User Request

Paste the user's request exactly or summarize only if it is very long. Preserve paths, filenames, and acceptance phrases.

## Codex Interpretation

- Goal:
- Non-goals:
- User-visible result:
- Risk level:

## Context Pack

### Architecture Summary

Describe only the relevant modules and flow.

### Relevant Files

| Path | Why it matters | Read/write |
| --- | --- | --- |
|  |  | read |
|  |  | write |

### Existing Contracts

- CLI/API/schema contracts:
- State files:
- Test conventions:
- Artifact locations:

### Prior Failures Or Review Findings

- Finding:
- Evidence:
- Required repair:

## Global Constraints

- This task must be implemented by Reasonix through the declared transport.
- A generic Codex subagent, Codex worker, or forked Codex thread is not a valid Reasonix substitute.
- If the declared transport cannot be invoked, return `BLOCKED` instead of implementing with Codex.
- Do not hardcode sample-specific content.
- Do not weaken or delete tests.
- Do not modify public APIs unless this packet explicitly allows it.
- Do not edit files outside the allowed paths.
- Do not commit, push, or deploy unless explicitly requested.
- Preserve existing behavior outside the requested scope.

## Tasklets

### Tasklet T1: <specific title>

- Goal:
- Allowed files:
- Forbidden files:
- Required edits:
- Required tests:
- Commands to run:
- Acceptance checks:
- Evidence to return:
- Stop/block conditions:

### Tasklet T2: <specific title>

- Goal:
- Allowed files:
- Forbidden files:
- Required edits:
- Required tests:
- Commands to run:
- Acceptance checks:
- Evidence to return:
- Stop/block conditions:

## Worker Self-QA Gate

Before returning to Codex, the worker must verify:

- [ ] Implementation matches every tasklet.
- [ ] No forbidden files were changed.
- [ ] No sample-specific hardcoding was added.
- [ ] Required tests were run or a concrete blocker is documented.
- [ ] Generated artifacts exist at the claimed paths.
- [ ] Build report only summarizes; it does not paste huge code or logs.

## Self-Score

Return this JSON:

```json
{
  "status": "READY_FOR_CODEX_REVIEW",
  "scores": {
    "implementation_completeness": 0,
    "test_coverage": 0,
    "evidence_quality": 0,
    "risk_control": 0,
    "instruction_following": 0
  },
  "changed_files": [],
  "generated_artifacts": [],
  "test_log_paths": [],
  "evidence_paths": [],
  "known_risks": [],
  "summary": ""
}
```

If any score is below 4, required evidence is missing, or a required test fails, return `NEEDS_FIX`, `BLOCKED`, or `NEEDS_CLARIFICATION` instead of `READY_FOR_CODEX_REVIEW`.

## Build Report Rules

Return only:

- What changed.
- What files changed.
- What artifacts were generated.
- What tests/commands ran.
- Where the evidence is.
- Known risks.
- Self-score.

Do not paste large source files, long logs, or full chain-of-thought.

## Fix-Round Format

If Codex requests changes, the worker should receive only:

- Failed check.
- Evidence Codex used.
- Exact repair instruction.
- Required command/test.
- Required evidence path.
- Stop condition.
```
