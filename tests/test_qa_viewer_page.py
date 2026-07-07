import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_qa_viewer_page_is_independent_and_uses_current_stream():
    client = server.app.test_client()

    response = client.get("/qa-viewer")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Handoff Lab" in html
    assert 'new EventSource("/stream")' in html
    assert 'fetch("/api/qa-events")' in html
    assert 'fetch("/api/qa-watch"' in html
    assert 'fetch("/api/dev-rehearsal"' in html
    assert "qa_external" in html
    assert "打开主面板" not in html
    assert "location.reload()" in html
    assert "event-detail" in html
    assert "formatDetail" in html
    assert "KIND_ICON" in html
    assert "removeDuplicateWorkspaceSnapshots" in html


def test_qa_viewer_page_renders_watch_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_qa_watch_workspace", str(tmp_path))
    client = server.app.test_client()

    response = client.get("/qa-viewer")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'value="{tmp_path}"' in html
    assert "loadWatchState" in html


def test_favicon_returns_no_content():
    client = server.app.test_client()

    response = client.get("/favicon.ico")

    assert response.status_code == 204


def test_root_redirects_to_qa_viewer():
    client = server.app.test_client()

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == "/qa-viewer"
