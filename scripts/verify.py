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
from data_store import content_hash

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data"


def verify(data_dir: Path) -> int:
    statutes_dir = data_dir / "statutes"
    index_path = data_dir / "index" / "laws.jsonl"

    if not statutes_dir.exists():
        print(f"❌ {statutes_dir} 不存在")
        return 2

    files = list(statutes_dir.glob("*.json"))
    if not files or not index_path.exists():
        print("❌ empty dataset or missing laws index")
        return 2
    print(f"扫描 {len(files)} 个 statutes 文件...")

    ok = 0
    bad = 0
    bad_list = []
    index_count = 0
    index_ids = set()
    index_hashes = {}
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                index_count += 1
                try:
                    entry = json.loads(line)
                    slug = entry["id"]
                    if slug in index_ids:
                        raise ValueError("duplicate index id")
                    index_ids.add(slug)
                    if "content_hash" in entry:
                        index_hashes[slug] = entry["content_hash"]
                except (ValueError, KeyError, TypeError):
                    bad += 1
                    bad_list.append(("laws.jsonl", f"invalid/duplicate row {index_count}"))
    file_ids = {path.stem for path in files}
    for slug in sorted(index_ids ^ file_ids):
        bad += 1
        bad_list.append((slug, "index/file set mismatch"))
    try:
        aliases = json.loads((data_dir / "index/slug-map.json").read_text())
        version_file = data_dir / "index/versions.json"
        versions = json.loads(version_file.read_text()) if version_file.exists() else {}
        for slug in aliases.values():
            if slug not in file_ids and slug not in versions:
                raise ValueError("alias target missing")
        for slugs in versions.values():
            if not slugs or not set(slugs) <= file_ids:
                raise ValueError("version target missing")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        bad += 1
        bad_list.append(("index", str(exc)))

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            bad += 1
            bad_list.append((f.name, f"JSON 解析失败: {e}"))
            continue
        # 校验 hash
        if not isinstance(data, dict):
            bad += 1
            bad_list.append((f.name, "JSON must be an object"))
            continue
        stored_hash = data.get("content_hash", "")
        canonical = json.dumps({k: v for k, v in data.items() if k != "content_hash"},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        actual_hash = content_hash(data)
        if f.stem in index_hashes and index_hashes[f.stem] != actual_hash:
            bad += 1
            bad_list.append((f.name, "published index hash mismatch"))
            continue
        if stored_hash != actual_hash:
            bad += 1
            bad_list.append((f.name, f"hash 不一致: stored={stored_hash[:20]} actual={actual_hash[:20]}"))
            continue
        # 校验结构
        if "id" not in data or "name" not in data or "articles" not in data:
            bad += 1
            bad_list.append((f.name, "缺少必需字段"))
            continue
        if (data["id"] != f.stem or data.get("slug") != f.stem
                or not isinstance(data["articles"], dict)
                or data.get("article_count") != len(data["articles"])):
            bad += 1
            bad_list.append((f.name, "identifier/article_count mismatch"))
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
