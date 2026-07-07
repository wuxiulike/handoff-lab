"""工作目录单一真相源契约测试。

workspace.json 是唯一权威。其它来源（tools.ai_flow.ROOT、active conversation
缓存）必须与之一致。固化以下契约，防止未来改动引入漂移：

1. 初始状态三源一致
2. save_workspace_root 后 ai_flow.ROOT 跟随
3. 切换 conversation → workspace.json 跟随该会话的工作目录
4. 用户主动切换目录 → active conversation 缓存同步
5. workspace_consistency() 正确诊断一致/不一致
"""
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_initial_consistency(tmp_path, monkeypatch):
    """启动后 workspace.json / ai_flow.ROOT / active conversation 三源一致。"""
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    server.save_workspace_root(tmp_path)
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})

    state = server.workspace_consistency()
    assert state["consistent"] is True
    assert Path(state["workspace_json"]) == tmp_path.resolve()
    assert state["ai_flow_root"] == state["workspace_json"]


def test_save_workspace_root_propagates_to_ai_flow(tmp_path, monkeypatch):
    """save_workspace_root 写 workspace.json 后，ai_flow.ROOT 必须跟随。"""
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")

    new_dir = tmp_path / "projectA"
    saved = server.save_workspace_root(new_dir)

    from tools import ai_flow
    assert str(ai_flow.ROOT) == str(saved)
    assert str(ai_flow.ROOT) == str(new_dir.resolve())


def test_activate_conversation_switches_workspace_json(tmp_path, monkeypatch):
    """切换 active conversation 后，workspace.json 跟随该会话记录的工作目录。"""
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")

    ws_a = tmp_path / "wsA"
    ws_b = tmp_path / "wsB"
    ws_a.mkdir()
    ws_b.mkdir()

    server.save_workspace_root(ws_a)
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})
    _, conv_a = server.create_conversation(title="对话A", workspace=str(ws_a))
    _, conv_b = server.create_conversation(title="对话B", workspace=str(ws_b))

    # 激活对话A → workspace.json 应指向 wsA
    server.activate_conversation(conv_a["id"])
    assert str(server.load_workspace_root()) == str(ws_a.resolve())

    # 激活对话B → workspace.json 应切换到 wsB
    server.activate_conversation(conv_b["id"])
    assert str(server.load_workspace_root()) == str(ws_b.resolve())

    state = server.workspace_consistency()
    assert state["consistent"] is True


def test_user_switch_workspace_updates_active_conversation(tmp_path, monkeypatch):
    """用户通过 save_workspace_root + update_active_workspace 切换目录后，
    active conversation 缓存与权威源一致。"""
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")

    initial = tmp_path / "initial"
    server.save_workspace_root(initial)
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})
    server.create_conversation(title="主对话", workspace=str(initial))

    new_dir = tmp_path / "switched"
    saved = server.save_workspace_root(new_dir)
    server.update_active_workspace(saved)

    state = server.workspace_consistency()
    assert state["consistent"] is True
    assert state["active_conversation"] == str(new_dir.resolve())


def test_workspace_consistency_detects_drift(tmp_path, monkeypatch):
    """当 ai_flow.ROOT 被直接篡改（绕过 server 层）时，workspace_consistency 报告不一致。"""
    monkeypatch.setattr(server, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    server.save_workspace_root(tmp_path)
    server.save_session({"mode": "dev_loop", "turns": [], "events": []})

    # 正常情况一致
    assert server.workspace_consistency()["consistent"] is True

    # 直接篡改 ai_flow.ROOT（模拟外部绕过），应被检测到
    from tools import ai_flow
    original = ai_flow.ROOT
    ai_flow.ROOT = Path("/nonexistent/drifted/path")
    try:
        state = server.workspace_consistency()
        assert state["consistent"] is False
        assert state["ai_flow_root"] != state["workspace_json"]
    finally:
        ai_flow.ROOT = original
