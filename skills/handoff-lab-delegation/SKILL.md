---
name: handoff-lab-delegation
description: Use when Codex should delegate software implementation to a real Handoff Lab worker transport while Codex only plans, writes detailed task packets, collects evidence, and performs QA without editing implementation code. Trigger for planner-worker coding workflows, Reasonix/DeepSeek-compatible implementation delegation, worker self-check/self-score gates, structured handoff packets, fix-round packets, and safe use of long-context workers.
---

# Handoff Lab Delegation

Use this skill to run a two-agent development loop:

- Codex is the architect, planner, task splitter, QA reviewer, and acceptance judge.
- The implementation worker edits code, runs commands, tests, and produces evidence.
- Codex must not edit implementation code in this workflow. Codex may read files,
  inspect diffs, inspect artifacts, run review commands, and write handoff/review
  documents.

## Operating Contract

1. Start from the user's request and convert it into a concrete implementation packet.
2. Make the packet specific enough that the worker does not need to invent architecture or infer hidden requirements.
3. Split complex work into small tasklets with allowed files, forbidden files, required tests, commands, acceptance checks, and evidence paths.
4. Require the worker to self-check before handing back to Codex.
5. Codex reviews only real evidence: changed files, diff, test logs, generated artifacts, reports, screenshots, or command output.
6. If evidence is missing, Codex must request a fix round instead of trusting the build report.

## Delegation Transport Gate

Before any implementation work starts, Codex must prove that the task was handed
to a real worker execution path. The preferred transport is the local Handoff Lab
web bridge.

Allowed transports:

1. Local Handoff Lab web bridge:
   - Set the target workspace with `POST /api/workspace`.
   - Optionally start viewer monitoring with `POST /api/qa-watch`.
   - Start the pipeline with `POST /api/start`.
   - The local service then invokes its configured implementation worker.
2. Direct worker CLI from the orchestrator repository:
   - Write the task into the orchestrator state/packet files.
   - Run the project command that calls the worker CLI, for example
     `python tools/ai_flow.py reasonix-build` from the Handoff Lab repository.
   - Capture `.agent/logs/reasonix_stdout.log` and build/evidence files.
3. A project-specific adapter that explicitly shells out to a configured worker
   executable such as `reasonix`, `reasonix.cmd`, `reasonix.exe`, or
   `REASONIX_CLI`, and produces evidence files.

Forbidden substitutes:

- Do not create a generic Codex subagent, worker, or forked Codex thread to implement the code.
- Do not ask another OpenAI model to act as the worker unless it actually invokes the configured worker CLI/adapter.
- Do not mark the build as delegated if there is no worker CLI/adapter log or evidence package.

If no allowed transport is available, stop and return `BLOCKED` with the exact
missing transport, for example `worker CLI not found` or `Handoff Lab web bridge
is not running`. Do not silently fall back to Codex editing code or a generic
worker.

## Workflow

1. Clarify the task only when execution would be unsafe or impossible without more input.
2. Read enough repository context to understand ownership boundaries and existing patterns.
3. Produce a `HANDOFF_LAB_IMPLEMENTATION_PACKET`.
4. Select an allowed transport and record it in the packet.
5. Send the packet through that transport. If the transport cannot be verified, return `BLOCKED`.
6. The worker implements, runs tests, produces a build report, and completes self-QA.
7. Codex reviews the evidence package.
8. If the worker fails the same Codex review finding three consecutive times, Codex may perform exactly one temporary fallback implementation pass for that task, then return to the normal worker path if more work remains.
9. Return one of:
   - `APPROVED`
   - `CHANGES_REQUESTED`
   - `BLOCKED`
   - `NEEDS_CLARIFICATION`
10. For fix rounds, send only failed checks, exact repair instructions, required evidence, and any updated constraints. Do not re-plan from scratch.

Codex reviews should use the Handoff Lab Codex QA profile: compact JSON for the
orchestrator, plus optional 7-section `guidance_markdown` when the next worker
round needs a fuller repair document.

## Long-Context Worker Strategy

Use a long-context worker as a structured context pack, not as a raw dump.

Include:

- Original user request.
- Codex interpretation and non-goals.
- Relevant architecture summary.
- Relevant files, with full contents only when needed.
- Existing APIs, schemas, CLI commands, tests, and state files.
- Prior failure logs and Codex review findings.
- Acceptance criteria and forbidden shortcuts.
- Evidence requirements.

Avoid:

- Whole-repo dumps without structure.
- Long unrelated logs.
- Hidden changes to task state files.
- Asking the worker to design large architecture from vague goals.

## Worker Self-QA Gate

The worker must not hand off to Codex review until it has produced:

- File change summary.
- Generated artifact summary.
- Real test log or explicit reason tests cannot run.
- Evidence paths.
- Known risks.
- Self-score.

Self-score fields are 0-5:

- `implementation_completeness`
- `test_coverage`
- `evidence_quality`
- `risk_control`
- `instruction_following`

If any score is below 4, any required test fails, or required evidence is
missing, the worker must return `NEEDS_FIX`, `BLOCKED`, or
`NEEDS_CLARIFICATION` instead of `READY_FOR_CODEX_REVIEW`.

## Output Statuses

Worker build report statuses:

- `READY_FOR_CODEX_REVIEW`: implementation and self-check passed.
- `NEEDS_FIX`: the worker found fixable issues and should continue.
- `BLOCKED`: external dependency, missing permission, missing tool, or impossible condition.
- `NEEDS_CLARIFICATION`: user/Codex input is insufficient.

Codex review statuses:

- `APPROVED`: evidence satisfies the acceptance criteria.
- `CHANGES_REQUESTED`: implementation or evidence is insufficient.
- `BLOCKED`: the loop cannot continue without external action.
- `NEEDS_CLARIFICATION`: the user must clarify scope or decision.

## References

Read `references/packet-template.md` when writing an implementation packet.

Read `references/review-rubric.md` when reviewing worker output.

Use `scripts/make_reasonix_packet.py` when a deterministic Markdown packet from
JSON is helpful.

Use `scripts/invoke_reasonix_web.py` when the local Handoff Lab bridge is
running and another Codex conversation needs to start a real worker-backed
pipeline:

```bash
python <skill_dir>/scripts/invoke_reasonix_web.py --workspace <project_dir> --task-file <project_dir>/.reasonix/HANDOFF_LAB_IMPLEMENTATION_PACKET_task.md --rounds 3
```

When `--task-file` is already a complete implementation packet, prefer direct
packet mode:

```bash
python <skill_dir>/scripts/invoke_reasonix_web.py --workspace <project_dir> --task-file <project_dir>/.reasonix/HANDOFF_LAB_IMPLEMENTATION_PACKET_task.md --rounds 1 --direct-reasonix
```

Direct packet mode skips extra Codex planning and hands the packet to the worker
immediately. Use normal mode only when Codex still needs to create the plan
first.

This script calls `/api/workspace`, `/api/qa-watch`, and `/api/start`. If
`/api/start` returns `authorization_required`, the script waits for the user to
approve the pending start in `/qa-viewer` and then continues automatically. Do
not replace the worker with a generic Codex worker while waiting.

Port handling:

- If `--base-url` is provided, the script uses it directly.
- Otherwise, if `HANDOFF_LAB_URL` is set, the script uses that value.
- Otherwise, the script probes common local ports through `/api/health` and
  prefers the service whose reported workspace matches `--workspace`.
- If multiple services are running and none matches the target workspace, stop
  and ask the user to set `HANDOFF_LAB_URL` or pass `--base-url`; do not guess.

Use `scripts/emit_viewer_event.py` when the local Handoff Lab viewer is running
and the user wants to see this workflow from another Codex conversation. Emit
compact lifecycle events, for example:

```bash
python <skill_dir>/scripts/emit_viewer_event.py --kind packet_written --label Codex --title "Worker task packet written" --detail "Path: <project_dir>/.reasonix/packet.md" --workspace "<project_dir>"
python <skill_dir>/scripts/emit_viewer_event.py --kind worker_started --label Worker --title "Worker started" --detail "Transport: Handoff Lab web bridge" --workspace "<project_dir>"
python <skill_dir>/scripts/emit_viewer_event.py --kind codex_review --label Codex --title "Codex review result" --detail "APPROVED or CHANGES_REQUESTED with evidence paths" --workspace "<project_dir>"
```

Do not send secrets, full source files, or long logs to the viewer. Send
summaries and paths only.

## Non-Negotiables

- Codex does not edit implementation code in this workflow.
- Exception: after the same review finding fails three consecutive worker attempts, Codex may make one temporary fallback implementation pass for that task only.
- Codex does not create generic Codex subagents or Codex worker threads to implement the code.
- Delegation is valid only when an allowed transport is used and evidence is produced.
- The worker does not approve its own work.
- Claims are not evidence.
- A missing test log is a review finding.
- A missing artifact is a review finding.
- Build reports must summarize; Codex can inspect files directly.
- Fix rounds should be narrow and evidence-driven.
