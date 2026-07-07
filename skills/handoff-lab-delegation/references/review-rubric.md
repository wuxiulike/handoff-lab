# Codex Review Rubric For Worker Output

Codex reviews evidence, not claims. Use this rubric before returning `APPROVED`.

## Required Evidence

- Non-empty changed file list or explicit no-code-change rationale.
- Real diff or directly inspected changed files.
- Build report with status and self-score.
- Test log path and actual test result, or documented blocker.
- Generated artifact paths when the task produces files.
- Quality reports, screenshots, previews, or render outputs when the task asks for visual quality.

## Review Steps

1. Compare the original user request with the Reasonix build report.
2. Inspect the changed files or diff directly.
3. Confirm generated artifacts exist at the claimed paths.
4. Read the actual test log instead of accepting a summary.
5. Check that task state files were not polluted by unrelated tasks.
6. Check for hardcoding of sample names, document titles, customer names, or one-off phrases.
7. Check that public APIs, schemas, and CLI behavior remain compatible unless the packet allowed changes.
8. Verify that the worker self-score is complete and honest.
9. Decide status.

## Decision Rules

Return `APPROVED` only when:

- Acceptance criteria are met.
- Required evidence exists and has been inspected.
- Tests pass or skipped tests have an acceptable, explicit reason.
- Known risks are non-blocking.

Return `CHANGES_REQUESTED` when:

- Evidence is missing.
- Test log is missing or inconsistent with the build report.
- Artifacts are missing.
- Diff does not match the claimed work.
- Visual or generated-output tasks lack previews or quality evidence.
- The implementation takes shortcuts forbidden by the packet.

Return `BLOCKED` when:

- A required external tool, permission, input file, credential, or environment dependency is missing.
- Continuing would be unsafe or misleading.

Return `NEEDS_CLARIFICATION` when:

- The user's goal cannot be executed without choosing scope, target files, or acceptance criteria.

## Fix-Round Instruction Style

Keep fix-round instructions narrow:

- Cite the failed check.
- Cite the evidence.
- State the exact required repair.
- State the command or artifact needed for verification.
- Do not ask Reasonix to redo unrelated planning.

When the review needs another worker round, prefer a compact 7-section
`guidance_markdown` block plus short `fix_instructions`. The guidance should
cover:

1. Conclusion.
2. What Codex verified.
3. Passed items.
4. Failed items.
5. Next development guidance.
6. Required evidence for the next round.
7. Boundaries and final judgment.

This mirrors the standalone Codex QA pattern while keeping Handoff Lab as the
single orchestration and viewer service.

## Common Failure Patterns

- Build report says tests passed, but the evidence package says `No test command configured`.
- Artifact is claimed, but path does not exist.
- The worker pasted a long process transcript instead of a concise report.
- Diff only touches `.agent` state files while the report claims feature work.
- Generated PPT/PDF/image quality is approved without real preview or render evidence.
- A new conversation accidentally reuses old context or old working directory.
