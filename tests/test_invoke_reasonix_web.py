import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "handoff-lab-delegation" / "scripts" / "invoke_reasonix_web.py"
spec = importlib.util.spec_from_file_location("invoke_reasonix_web", SCRIPT)
invoke_reasonix_web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(invoke_reasonix_web)


def test_discover_base_url_prefers_matching_workspace(monkeypatch, tmp_path):
    monkeypatch.delenv("HANDOFF_LAB_URL", raising=False)
    target = str(tmp_path.resolve())

    def fake_get_json(base_url, path, timeout=5):
        assert path == "/api/health"
        if base_url.endswith(":51514"):
            return {"ok": True, "workspace": "H:\\other_project"}
        if base_url.endswith(":51515"):
            return {"ok": True, "workspace": target}
        return {}

    monkeypatch.setattr(invoke_reasonix_web, "get_json", fake_get_json)

    assert invoke_reasonix_web.discover_base_url(target) == "http://127.0.0.1:51515"


def test_discover_base_url_uses_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_LAB_URL", "http://127.0.0.1:59999")

    assert invoke_reasonix_web.discover_base_url(str(tmp_path)) == "http://127.0.0.1:59999"


def test_discover_base_url_rejects_ambiguous_services(monkeypatch, tmp_path):
    monkeypatch.delenv("HANDOFF_LAB_URL", raising=False)

    def fake_get_json(base_url, path, timeout=5):
        assert path == "/api/health"
        if base_url.endswith(":51514"):
            return {"ok": True, "workspace": "H:\\old_service"}
        if base_url.endswith(":51515"):
            return {"ok": True, "workspace": "H:\\another_service"}
        return {}

    monkeypatch.setattr(invoke_reasonix_web, "get_json", fake_get_json)

    try:
        invoke_reasonix_web.discover_base_url(str(tmp_path))
    except RuntimeError as exc:
        message = str(exc)
        assert "HANDOFF_LAB_URL" in message
        assert "--base-url" in message
    else:
        raise AssertionError("expected ambiguous service discovery to fail")
