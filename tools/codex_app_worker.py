import json
import os
import subprocess
import threading
import uuid
from collections import deque
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

def build_codex_app_server_command() -> str:
    codex_cli = os.environ.get("CODEX_CLI", "codex")
    return (
    f'"{codex_cli}" app-server --stdio '
    '-c model_provider="openai-no-ws" '
    '-c model_providers.openai-no-ws.name="OpenAI" '
    '-c model_providers.openai-no-ws.base_url="https://chatgpt.com/backend-api/codex" '
    '-c model_providers.openai-no-ws.wire_api="responses" '
    '-c model_providers.openai-no-ws.requires_openai_auth=true '
    '-c model_providers.openai-no-ws.supports_websockets=false'
    )


CODEX_APP_SERVER_COMMAND = build_codex_app_server_command()


class StdioTransport:
    def __init__(self, command, cwd, env=None):
        self.proc = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )

    def write(self, payload):
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise EOFError("codex app-server closed stdout")
        return json.loads(line)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()


class CodexAppWorker:
    def __init__(self, transport=None, command=None, cwd=None, env=None):
        self.transport = transport
        self.command = command or build_codex_app_server_command()
        self.cwd = Path(cwd) if cwd else ROOT
        self.env = env
        self._lock = threading.Lock()
        self._request_counter = 0
        self._pending_notifications = deque()
        self._initialized = False
        self.thread_id = None

    def start(self):
        if self.transport is None:
            self.transport = StdioTransport(self.command, self.cwd, self.env)
        if not self._initialized:
            self.initialize()

    def initialize(self):
        response = self._request("initialize", {
            "clientInfo": {
                "name": "codex-reasonix-worker",
                "title": "Handoff Lab Worker",
                "version": "0.1.0",
            },
            "capabilities": {
                "experimentalApi": True,
                "requestAttestation": False,
                "optOutNotificationMethods": [],
            },
        })
        self._write({"method": "initialized"})
        self._initialized = True
        return response

    def ask(self, prompt, on_token=None):
        with self._lock:
            self.start()
            if not self.thread_id:
                thread = self._request("thread/start", {
                    "cwd": str(self.cwd),
                    "ephemeral": False,
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                })
                self.thread_id = thread["thread"]["id"]

            turn = self._request("turn/start", {
                "threadId": self.thread_id,
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "input": [{
                    "type": "text",
                    "text": prompt,
                    "text_elements": [],
                }],
            })
            turn_id = turn["turn"]["id"]
            return self._stream_turn(turn_id, on_token=on_token)

    def close(self):
        if self.transport and hasattr(self.transport, "close"):
            self.transport.close()

    def _stream_turn(self, turn_id, on_token=None):
        captured = []
        while True:
            message = self._next_notification()
            method = message.get("method")
            params = message.get("params") or {}
            if method in {"agent/message/delta", "item/agentMessage/delta"} and params.get("turnId") == turn_id:
                delta = params.get("delta", "")
                if delta:
                    captured.append(delta)
                    if on_token:
                        on_token(delta)
            elif method == "turn/completed" and (params.get("turn") or {}).get("id") == turn_id:
                status = (params.get("turn") or {}).get("status")
                if status not in {None, "completed"}:
                    raise RuntimeError(f"Codex turn ended with status: {status}")
                return "".join(captured).strip()
            elif "id" in message and "method" in message:
                self._handle_server_request(message)

    def _handle_server_request(self, request):
        method = request.get("method")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "permissions/requestApproval",
        }:
            self._write({"id": request["id"], "result": {"decision": "denied"}})
            return
        if method == "item/tool/requestUserInput":
            self._write({"id": request["id"], "result": {"answers": {}}})
            return
        self._write({"id": request["id"], "error": {"code": -32601, "message": f"unsupported request: {method}"}})

    def _request(self, method, params):
        request_id = self._next_request_id()
        self._write({"id": request_id, "method": method, "params": params})
        return self._wait_for_response(request_id, method)

    def _wait_for_response(self, request_id, method):
        while True:
            message = self._read()
            if message.get("id") == request_id and "result" in message:
                return message["result"]
            if message.get("id") == request_id and "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            if "method" in message and "id" not in message:
                self._pending_notifications.append(message)
            elif "method" in message and "id" in message:
                self._handle_server_request(message)

    def _next_notification(self):
        if self._pending_notifications:
            return self._pending_notifications.popleft()
        while True:
            message = self._read()
            if "method" in message and "id" not in message:
                return message
            if "method" in message and "id" in message:
                self._handle_server_request(message)

    def _next_request_id(self):
        self._request_counter += 1
        return str(self._request_counter)

    def _write(self, payload):
        self.transport.write(payload)

    def _read(self):
        return self.transport.read()
