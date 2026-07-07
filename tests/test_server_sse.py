import importlib.util
import queue
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_stream_sends_connected_event_immediately():
    server._event_queue.clear()

    with server.app.test_request_context("/stream"):
        response = server.stream()

    chunks = queue.Queue()
    def read_first_chunk():
        chunk = next(response.response)
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        chunks.put(chunk)

    thread = threading.Thread(target=read_first_chunk, daemon=True)
    thread.start()
    try:
        first_chunk = chunks.get(timeout=1)
    except queue.Empty:
        raise AssertionError("stream did not send an initial event")

    assert response.status_code == 200
    assert "event: connected" in first_chunk
