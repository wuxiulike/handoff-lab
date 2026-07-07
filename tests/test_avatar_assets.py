from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_avatar_assets_exist_and_template_uses_them():
    assert (ROOT / "static" / "avatars" / "chatgpt.png").exists()
    assert (ROOT / "static" / "avatars" / "deepseek.png").exists()
