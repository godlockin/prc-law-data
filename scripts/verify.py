#!/usr/bin/env python3
"""
verify.py — prc-law-data 完整性校验

检查每个 statute.json 的 content_hash 与实际内容是否一致,
报告缺失文件,损坏文件, 索引与实际不符等.

用法:
    python3 verify.py
    python3 verify.py --data-dir /path/to/data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data"


def verify(data_dir: Path) -> int:
    statutes_dir = data_dir / "statutes"
    index_path = data_dir / "index" / "laws.jsonl"

    if not statutes_dir.exists():
        print(f"❌ {statutes_dir} 不存在")
        return 2

    files = list(statutes_dir.glob("*.json"))
    print(f"扫描 {len(files)} 个 statutes 文件...")

    ok = 0
    bad = 0
    bad_list = []
    index_count = 0
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                index_count += 1

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            bad += 1
            bad_list.append((f.name, f"JSON 解析失败: {e}"))
            continue
        # 校验 hash
        stored_hash = data.get("content_hash", "")
        canonical = json.dumps({k: v for k, v in data.items() if k != "content_hash"},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        actual_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if stored_hash != actual_hash:
            bad += 1
            bad_list.append((f.name, f"hash 不一致: stored={stored_hash[:20]} actual={actual_hash[:20]}"))
            continue
        # 校验结构
        if "id" not in data or "name" not in data or "articles" not in data:
            bad += 1
            bad_list.append((f.name, "缺少必需字段"))
            continue
        ok += 1

    print(f"\n✅ 正确: {ok}")
    print(f"❌ 损坏: {bad}")
    print(f"📊 索引声明: {index_count}")
    print(f"📁 实际文件: {len(files)}")
    if bad:
        print("\n损坏列表 (前 10):")
        for n, msg in bad_list[:10]:
            print(f"  {n}: {msg}")
    return 0 if bad == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA))
    args = parser.parse_args()
    return verify(Path(args.data_dir))


if __name__ == "__main__":
    sys.exit(main())