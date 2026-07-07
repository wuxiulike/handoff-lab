import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def use_temp_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    server.save_workspace_root(tmp_path)


def test_file_preview_returns_text_content(tmp_path, monkeypatch):
    use_temp_workspace(tmp_path, monkeypatch)
    sample = tmp_path / "sample.json"
    sample.write_text('{"ok": true}', encoding="utf-8")

    client = server.app.test_client()
    response = client.get("/api/file", query_string={"path": "sample.json"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "text"
    assert payload["path"] == "sample.json"
    assert payload["content"] == '{"ok": true}'


def test_file_preview_returns_html_content(tmp_path, monkeypatch):
    use_temp_workspace(tmp_path, monkeypatch)
    page = tmp_path / "index.html"
    page.write_text("<h1>Hello</h1>", encoding="utf-8")

    client = server.app.test_client()
    response = client.get("/api/file", query_string={"path": "index.html"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "html"
    assert payload["content"] == "<h1>Hello</h1>"


def test_file_raw_serves_images(tmp_path, monkeypatch):
    use_temp_workspace(tmp_path, monkeypatch)
    image = tmp_path / "pixel.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
    )

    client = server.app.test_client()
    response = client.get("/api/file/raw", query_string={"path": "pixel.png"})

    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")


def test_file_preview_rejects_path_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path / "workspace")
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    server.ROOT.mkdir()
    server.save_workspace_root(server.ROOT)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    client = server.app.test_client()
    response = client.get("/api/file", query_string={"path": str(outside)})

    assert response.status_code == 400
    assert response.get_json()["error"] == "path outside workspace"
