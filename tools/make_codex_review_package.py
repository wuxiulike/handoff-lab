#!/usr/bin/env python3
"""独立生成 Codex 验收包 —— 用于调试或手动检查。

Usage:
  python tools/make_codex_review_package.py          # 输出到 stdout
  python tools/make_codex_review_package.py --save   # 保存到 .agent/outbox/to_codex_review.md
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ai_flow import make_codex_review_package, write_text

if __name__ == "__main__":
    save = "--save" in sys.argv
    prompt = make_codex_review_package()

    if save:
        write_text(".agent/outbox/to_codex_review.md", prompt)
        print("Saved to .agent/outbox/to_codex_review.md")
    else:
        print(prompt)
