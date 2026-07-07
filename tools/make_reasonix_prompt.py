#!/usr/bin/env python3
"""独立生成 Reasonix 施工提示词 —— 用于调试或手动检查。

Usage:
  python tools/make_reasonix_prompt.py          # 输出到 stdout
  python tools/make_reasonix_prompt.py --save   # 保存到 .agent/outbox/to_reasonix.md
"""

import sys
from pathlib import Path

# 直接复用 ai_flow 的核心函数
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ai_flow import make_reasonix_prompt, write_text

if __name__ == "__main__":
    save = "--save" in sys.argv
    prompt = make_reasonix_prompt()

    if save:
        write_text(".agent/outbox/to_reasonix.md", prompt)
        print("Saved to .agent/outbox/to_reasonix.md")
    else:
        print(prompt)
