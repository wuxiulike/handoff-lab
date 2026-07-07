import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_collect_qa_workspace_snapshot_finds_reasonix_evidence(tmp_path):
    evidence = tmp_path / ".reasonix" / "evidence" / "case"
    evidence.mkdir(parents=True)
    (evidence / "build_report.md").write_text("READY_FOR_CODEX_REVIEW", encoding="utf-8")
    (evidence / "test.log").write_text("passed", encoding="utf-8")

    snapshot = server.collect_qa_workspace_snapshot(tmp_path)

    paths = [item["path"] for item in snapshot["files"]]
    assert ".reasonix/evidence/case/build_report.md" in paths
    assert ".reasonix/evidence/case/test.log" in paths


def test_collect_reasonix_outputs_prefers_build_report(tmp_path):
    evidence = tmp_path / ".reasonix" / "evidence" / "case"
    evidence.mkdir(parents=True)
    (evidence / "test.log").write_text("tests passed", encoding="utf-8")
    (evidence / "build_report.md").write_text("READY_FOR_CODEX_REVIEW\nReasonix says hello", encoding="utf-8")

    outputs = server.collect_reasonix_outputs(tmp_path)

    assert outputs[0]["path"] == ".reasonix/evidence/case/build_report.md"
    assert "Reasonix says hello" in outputs[0]["content"]


def test_collect_reasonix_outputs_deduplicates_nested_roots(tmp_path):
    evidence = tmp_path / ".reasonix" / "evidence" / "case"
    evidence.mkdir(parents=True)
    (evidence / "build_report.md").write_text("READY_FOR_CODEX_REVIEW", encoding="utf-8")

    outputs = server.collect_reasonix_outputs(tmp_path)
    paths = [output["path"] for output in outputs]

    assert paths.count(".reasonix/evidence/case/build_report.md") == 1


def test_collect_reasonix_outputs_excludes_runtime_noise(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "build_report.md").write_text("READY_FOR_CODEX_REVIEW", encoding="utf-8")
    (agent / "session.json").write_text('{"huge":"runtime"}', encoding="utf-8")
    (agent / "to_codex_review.md").write_text("review prompt", encoding="utf-8")

    outputs = server.collect_reasonix_outputs(tmp_path)
    paths = [output["path"] for output in outputs]

    assert ".agent/build_report.md" in paths
    assert ".agent/session.json" not in paths
    assert ".agent/to_codex_review.md" not in paths


def test_emit_qa_workspace_snapshot_sends_summary(monkeypatch, tmp_path):
    evidence = tmp_path / ".reasonix" / "evidence" / "case"
    evidence.mkdir(parents=True)
    (evidence / "build_report.md").write_text("READY_FOR_CODEX_REVIEW", encoding="utf-8")
    emitted = []
    monkeypatch.setattr(server, "emit", lambda event, data: emitted.append((event, data)))

    server.emit_qa_workspace_snapshot(tmp_path)

    assert emitted[0][0] == "qa_external"
    assert emitted[0][1]["kind"] == "workspace_snapshot"
    assert "build_report.md" in emitted[0][1]["detail"]


def test_qa_watch_api_rejects_missing_workspace():
    client = server.app.test_client()

    response = client.post("/api/qa-watch", json={"workspace": "Z:/not-exists"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "workspace_not_found"
