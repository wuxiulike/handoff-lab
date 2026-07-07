import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_summarize_reasonix_result_uses_report_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    server.save_workspace_root(tmp_path)
    (tmp_path / ".agent").mkdir(exist_ok=True)
    (tmp_path / ".agent" / "build_report.md").write_text(
        "## files_changed\n- app.py: created\n",
        encoding="utf-8",
    )
    (tmp_path / "deck.pptx").write_bytes(b"pptx")
    (tmp_path / "report.json").write_text('{"ok": true}', encoding="utf-8")

    summary = server.summarize_reasonix_result()

    assert "app.py: created" in summary
    assert "deck.pptx" in summary
    assert "report.json" in summary
    assert ".agent/logs/reasonix_stdout.log" in summary
