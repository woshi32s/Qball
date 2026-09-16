#!/usr/bin/env python3
"""发布流程用:更新仓库根目录的 version.json。

用法: python scripts/update_version.py <version> <url> <sha256> [notes]
说明: notes 建议留空(脚本内置默认中文文案),避免 CI 传参编码问题。
"""
import json
import sys
from pathlib import Path

MIRROR_PREFIXES = [
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
]

DEFAULT_NOTES = "本版本更新:安装器与 qball update 会自动拉取本版本"


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    version, url, sha = sys.argv[1:4]
    notes = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4].strip() else DEFAULT_NOTES
    data = {
        "version": version,
        "url": url,
        "sha256": sha.lower(),
        "mirrors": [p + url for p in MIRROR_PREFIXES],
        "notes": notes,
    }
    out = Path(__file__).resolve().parent.parent / "version.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
