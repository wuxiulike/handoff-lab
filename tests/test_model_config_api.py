import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_model_config_saves_without_echoing_api_key(tmp_path, monkeypatch):
    server.CONFIG_FILE = tmp_path / "model_config.json"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    client = server.app.test_client()
    response = client.post("/api/model-config", json={
        "openai_profile": "work",
        "openai_model": "gpt-5",
        "openai_reasoning": "high",
        "deepseek_base_url": "https://example.test",
        "deepseek_model": "deepseek-v4-pro",
        "deepseek_api_key": "sk-test",
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["openai_profile"] == "work"
    assert payload["openai_model"] == "gpt-5"
    assert payload["openai_reasoning"] == "high"
    assert payload["deepseek_base_url"] == "https://example.test"
    assert payload["deepseek_model"] == "deepseek-v4-pro"
    assert payload["deepseek_api_key_set"] is True
    assert "sk-test" not in response.get_data(as_text=True)


def test_model_config_rejects_unsupported_deepseek_model(tmp_path):
    server.CONFIG_FILE = tmp_path / "model_config.json"
    client = server.app.test_client()

    response = client.post("/api/model-config", json={"deepseek_model": "unknown-model"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "unsupported deepseek model"


def test_model_config_accepts_legacy_deepseek_models_until_deprecation(tmp_path):
    server.CONFIG_FILE = tmp_path / "model_config.json"
    client = server.app.test_client()

    response = client.post("/api/model-config", json={"deepseek_model": "deepseek-reasoner"})

    assert response.status_code == 200
    assert response.get_json()["deepseek_model"] == "deepseek-reasoner"


def test_apply_auth_env_distinguishes_allow_and_yolo(tmp_path):
    server.AUTH_FILE = tmp_path / "auth.json"

    server.save_auth({"mode": "allow"})
    server.apply_auth_env()
    assert os.environ["REASONIX_YOLO"] == "0"

    server.save_auth({"mode": "yolo"})
    server.apply_auth_env()
    assert os.environ["REASONIX_YOLO"] == "1"
