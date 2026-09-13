#!/usr/bin/env python3
"""发布流程用:更新仓库根目录的 version.json。

用法: python scripts/update_version.py <version> <url> <sha256> [notes]
"""
import json
import sys
from pathlib import Path

MIRROR_PREFIXES = [
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
]


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    version, url, sha = sys.argv[1:4]
    notes = sys.argv[4] if len(sys.argv) > 4 else ""
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
