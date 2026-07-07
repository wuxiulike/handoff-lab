import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_get_port_uses_default(monkeypatch):
    monkeypatch.delenv("HANDOFF_LAB_PORT", raising=False)

    assert server.get_port() == 51514


def test_get_port_accepts_env(monkeypatch):
    monkeypatch.setenv("HANDOFF_LAB_PORT", "51515")

    assert server.get_port() == 51515


def test_get_port_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("HANDOFF_LAB_PORT", "not-a-port")

    try:
        server.get_port()
    except ValueError as exc:
        assert "integer" in str(exc)
    else:
        raise AssertionError("expected ValueError")
