#!/usr/bin/env python3
"""Emit a compact event to the Handoff Lab QA viewer."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Send an event to a Handoff Lab QA viewer.")
    parser.add_argument("--kind", default="external")
    parser.add_argument("--label", default="External")
    parser.add_argument("--title", required=True)
    parser.add_argument("--detail", default="")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--conversation-id", default="")
    default_url = os.environ.get("HANDOFF_LAB_URL", "http://127.0.0.1:51514").rstrip("/") + "/api/qa-event"
    parser.add_argument("--url", default=default_url)
    args = parser.parse_args()

    payload = {
        "kind": args.kind,
        "label": args.label,
        "title": args.title,
        "detail": args.detail,
        "workspace": args.workspace,
        "conversation_id": args.conversation_id,
    }
    request = urllib.request.Request(
        args.url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            print(response.read().decode("utf-8", errors="replace"))
            return 0
    except urllib.error.URLError as exc:
        print(f"viewer_event_failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
