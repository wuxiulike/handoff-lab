"""session.json 归档/截断机制测试。

覆盖：
- 体积超 SESSION_MAX_BYTES → 归档最旧非活跃对话
- 单对话 turns 超 SESSION_MAX_TURNS → 触发归档
- active conversation 永不被归档
- 归档文件完整写入磁盘
- archived 元数据正确、archived_meta() 可读
"""
import importlib.util
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _make_conversation(server, cid, title, turn_count=1, updated_at=None, workspace=None):
    """构造一个含指定 turn 数的 conversation。"""
    conv = server._new_conversation(title=title, workspace=workspace)
    conv["id"] = cid
    conv["created_at"] = updated_at or time.time()
    conv["updated_at"] = updated_at or time.time()
    conv["turns"] = [
        {"role": "user", "text": f"{title}-turn-{i}", "ts": (updated_at or time.time()) + i,
         "messages": [{"role": "user", "content": f"{title}-msg-{i}"}]}
        for i in range(turn_count)
    ]
    conv["events"] = []
    return conv


def test_size_threshold_archives_oldest_non_active(tmp_path, monkeypatch):
    """session.json 体积超阈值时，归档最旧的非活跃对话。"""
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    # 用很小的阈值方便触发
    monkeypatch.setattr(server, "SESSION_MAX_BYTES", 500)
    monkeypatch.setattr(server, "SESSION_MAX_TURNS", 10000)

    base = time.time()
    old_conv = _make_conversation(server, "old-1", "旧对话", turn_count=2, updated_at=base)
    active_conv = _make_conversation(server, "active-1", "活跃对话", turn_count=2, updated_at=base + 100)

    session = {"mode": "dev_loop", "conversations": [old_conv, active_conv], "active_id": "active-1"}
    server.save_session(session)

    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    remaining_ids = [c["id"] for c in saved["conversations"]]
    # 旧对话应被归档（移除），活跃对话必须保留
    assert "old-1" not in remaining_ids
    assert "active-1" in remaining_ids
    # 写盘后体积应低于阈值
    assert len((tmp_path / "session.json").read_text(encoding="utf-8")) < 500 or len(saved["conversations"]) == 1
    # 归档元数据应记录 old-1
    archived_ids = [item["id"] for item in saved.get("archived", [])]
    assert "old-1" in archived_ids


def test_turns_threshold_triggers_archive(tmp_path, monkeypatch):
    """单个 conversation 的 turns 超阈值时触发归档。"""
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(server, "SESSION_MAX_BYTES", 10_000_000)  # 故意调高，不靠体积触发
    monkeypatch.setattr(server, "SESSION_MAX_TURNS", 3)

    base = time.time()
    long_conv = _make_conversation(server, "long-1", "超长对话", turn_count=10, updated_at=base)
    active_conv = _make_conversation(server, "active-1", "活跃对话", turn_count=1, updated_at=base + 100)

    session = {"mode": "dev_loop", "conversations": [long_conv, active_conv], "active_id": "active-1"}
    server.save_session(session)

    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    remaining_ids = [c["id"] for c in saved["conversations"]]
    # 超长对话应被归档，活跃对话保留
    assert "long-1" not in remaining_ids
    assert "active-1" in remaining_ids
    archived_ids = [item["id"] for item in saved.get("archived", [])]
    assert "long-1" in archived_ids


def test_active_conversation_never_archived_even_if_huge(tmp_path, monkeypatch):
    """即使活跃对话本身超阈值，也永不被归档。"""
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(server, "SESSION_MAX_BYTES", 10_000_000)
    monkeypatch.setattr(server, "SESSION_MAX_TURNS", 2)

    base = time.time()
    # 只有活跃对话，且它本身超 turns 阈值
    active_conv = _make_conversation(server, "active-huge", "活跃超长", turn_count=50, updated_at=base)

    session = {"mode": "dev_loop", "conversations": [active_conv], "active_id": "active-huge"}
    server.save_session(session)

    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    # 唯一的活跃对话必须原样保留
    assert len(saved["conversations"]) == 1
    assert saved["conversations"][0]["id"] == "active-huge"
    assert saved.get("archived", []) == []


def test_archived_file_written_to_disk_complete(tmp_path, monkeypatch):
    """归档的 conversation 完整写入磁盘文件。"""
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(server, "SESSION_MAX_BYTES", 500)
    monkeypatch.setattr(server, "SESSION_MAX_TURNS", 10000)

    base = time.time()
    old_conv = _make_conversation(server, "old-archive", "待归档", turn_count=3, updated_at=base)
    active_conv = _make_conversation(server, "active-1", "活跃", turn_count=1, updated_at=base + 100)

    session = {"mode": "dev_loop", "conversations": [old_conv, active_conv], "active_id": "active-1"}
    server.save_session(session)

    # 归档文件应存在，且内容完整（含原 turns）
    archive_files = list((tmp_path / "archive").rglob("old-archive.json"))
    assert len(archive_files) == 1
    archived_data = json.loads(archive_files[0].read_text(encoding="utf-8"))
    assert archived_data["id"] == "old-archive"
    assert len(archived_data["turns"]) == 3


def test_archived_meta_returns_metadata(tmp_path, monkeypatch):
    """archived_meta() 返回归档对话的只读元数据。"""
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(server, "SESSION_MAX_BYTES", 500)
    monkeypatch.setattr(server, "SESSION_MAX_TURNS", 10000)

    base = time.time()
    old_conv = _make_conversation(
        server, "old-meta", "归档元数据测试", turn_count=2, updated_at=base, workspace=str(tmp_path)
    )
    active_conv = _make_conversation(server, "active-1", "活跃", turn_count=1, updated_at=base + 100)

    session = {"mode": "dev_loop", "conversations": [old_conv, active_conv], "active_id": "active-1"}
    server.save_session(session)

    meta = server.archived_meta()
    assert len(meta) == 1
    assert meta[0]["id"] == "old-meta"
    assert meta[0]["title"] == "归档元数据测试"
    assert meta[0]["turn_count"] == 2


def test_small_session_not_archived(tmp_path, monkeypatch):
    """低于阈值的 session 不触发归档，行为与之前完全一致。"""
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ARCHIVE_DIR", tmp_path / "archive")
    # 用真实阈值
    monkeypatch.setattr(server, "SESSION_MAX_BYTES", 1_000_000)
    monkeypatch.setattr(server, "SESSION_MAX_TURNS", 200)

    server.save_session({"mode": "dev_loop", "turns": [], "events": []})
    server.append_session_turn("user", "一条普通消息")

    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert saved.get("archived", []) == []
    assert len(saved["conversations"]) == 1
