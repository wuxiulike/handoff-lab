import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.ai_flow import should_surface_codex_status, split_codex_status_lines


def test_surfaces_codex_reconnect_status_lines():
    assert should_surface_codex_status("Reconnecting 4/5")
    assert should_surface_codex_status("warning: Falling back from WebSockets to HTTPS transport.")
    assert should_surface_codex_status("request timed out")


def test_does_not_surface_regular_codex_noise_lines():
    assert not should_surface_codex_status("tokens used: 1234")
    assert not should_surface_codex_status("succeeded in 12s")


def test_splits_joined_codex_connection_status_lines():
    line = (
        "ERROR: Reconnecting... 2/5 ERROR: Reconnecting... 3/5 "
        "warning: Falling back from WebSockets to HTTPS transport. request timed out"
    )

    assert split_codex_status_lines(line) == [
        "ERROR: Reconnecting... 2/5",
        "ERROR: Reconnecting... 3/5",
        "warning: Falling back from WebSockets to HTTPS transport.",
        "request timed out",
    ]
