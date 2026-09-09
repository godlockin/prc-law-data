"""Dataset paths, hashes and publication helpers shared by maintenance commands."""
from __future__ import annotations
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from datetime import date
import common_bootstrap
from law_common.rules import choose_version


def dataset_fingerprint(data_dir: Path) -> str:
    """Identity of a published index set; edits must republish indexes."""
    digest = hashlib.sha256()
    for name in ("laws.jsonl", "versions.json", "slug-map.json"):
        digest.update(name.encode())
        digest.update((data_dir / "index" / name).read_bytes())
    return digest.hexdigest()


def statute_path(data_dir: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise ValueError("invalid statute identifier")
    root = (data_dir / "statutes").resolve()
    target = (root / f"{slug}.json").resolve()
    if not target.is_relative_to(root):
        raise ValueError("statute path escapes data directory")
    return target


def content_hash(data: dict) -> str:
    value = {key: val for key, val in data.items() if key != "content_hash"}
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def rebuild_indexes(data_dir: Path) -> int:
    entries, aliases, versions, articles = [], {}, {}, []
    by_law: dict[str, list[dict]] = {}
    for path in sorted((data_dir / "statutes").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("id") != path.stem or data.get("slug") != path.stem:
            raise ValueError(f"identifier mismatch: {path.name}")
        base = data.get("law_id", data["slug"])
        by_law.setdefault(base, []).append(data)
        versions.setdefault(base, []).append(data["slug"])
        entries.append({"id": data["slug"], "slug": data["slug"], "name": data["name"],
                        "short_name": data.get("short_name", data["name"]),
                        "law_id": base, "effective_date": data.get("effective_date", ""),
                        "type": data.get("type", ""), "article_count": len(data["articles"]),
                        "status": data.get("status", "unknown"),
                        "retrieval_status": data.get("retrieval_status", "legacy"),
                        "content_hash": content_hash(data),
                        "source": data.get("source", {}).get("via", ""),
                        "size_bytes": path.stat().st_size,
                        "updated_at": data.get("source", {}).get("fetched_at", "")})
        for key in data.get("articles_by_int", {}):
            articles.append({"law_slug": data["slug"], "article_num": key, "key_type": "int"})
        for key in data["articles"]:
            articles.append({"law_slug": data["slug"], "article_num": key, "key_type": "cn"})
    for base, items in by_law.items():
        for item in items:
            aliases[item["name"]] = base
            aliases[item.get("short_name", item["name"])] = base
    directory = data_dir / "index"
    for filename, value in [("slug-map.json", aliases), ("versions.json", versions)]:
        atomic_write(directory / filename, json.dumps(value, ensure_ascii=False, indent=2))
    for filename, rows in [("laws.jsonl", entries), ("articles.jsonl", articles)]:
        atomic_write(directory / filename, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return len(entries)
