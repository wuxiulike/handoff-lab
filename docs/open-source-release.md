# Open Source Release Checklist

Use this checklist before pushing Handoff Lab to a public repository.

## Must Pass

- [ ] `python -m pytest -q` passes in a clean clone.
- [ ] `python server.py` starts with default `127.0.0.1:51514`.
- [ ] `HANDOFF_LAB_PORT=51515 python server.py` starts on the alternate port.
- [ ] `sh ./start_handoff_lab.sh` starts on macOS/Linux.
- [ ] `/qa-viewer` loads and can receive at least one event.
- [ ] `skills/handoff-lab-delegation` can be copied into a Codex skills folder.
- [ ] The skill can submit a direct packet through `scripts/invoke_reasonix_web.py`.

## Repository Hygiene

- [ ] No `.agent/`, `.reasonix/`, `.pytest_cache/`, `__pycache__/`, log files, or generated output files are tracked.
- [ ] No `.env`, token, API key, auth, or model config file is tracked.
- [ ] No private absolute path is required for installation or normal operation.
- [ ] Local-only notes are removed, rewritten, or clearly marked as non-release material.
- [ ] Third-party names appear only as optional integration descriptions.
- [ ] README links and command examples work from a fresh clone.

## Security Boundary

- [ ] README states that this is a local-only tool.
- [ ] README explains that `yolo` mode grants broad command execution power.
- [ ] The default bind host remains `127.0.0.1`.
- [ ] The project does not bundle third-party CLI binaries or credentials.
