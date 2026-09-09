#!/usr/bin/env python3
"""
build_sqlite.py — prc-law-data SQLite 数据库构建器 (W12)

读 data/statutes/<slug>.json + data/index/laws.jsonl, 构建 SQLite:
- laws (法律元数据)
- articles (法条 + 全文索引 FTS5)
- statutes_meta (指向原始 JSON 路径)

输出: data/prc-law.db

兼容 PRC-Law DatasetClient: sqlite 模式下同样返回 DatasetHit 格式.
但本期 DatasetClient 仅读 JSON (优先); SQLite 后续可作为加速层.

用法:
    python3 build_sqlite.py
    python3 build_sqlite.py --data-dir /path/to/data --output /path/to/db
    python3 build_sqlite.py --verify  # 构建后跑完整性校验
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import os
import tempfile
from contextlib import closing
from data_store import dataset_fingerprint, content_hash
from verify import verify
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data"
DEFAULT_DB = DEFAULT_DATA / "prc-law.db"


SCHEMA = """
-- 法律元数据
CREATE TABLE IF NOT EXISTS laws (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    type          TEXT,
    status        TEXT,                  -- effective / superseded / draft
    source_via    TEXT,                  -- 上游来源 (laws-data / hf / lawrefbook)
    source_url    TEXT,
    fetched_at    TEXT,
    updated_at    TEXT,
    article_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_laws_slug ON laws(slug);
CREATE INDEX IF NOT EXISTS idx_laws_type ON laws(type);
CREATE INDEX IF NOT EXISTS idx_laws_status ON laws(status);

-- 法条
CREATE TABLE IF NOT EXISTS articles (
    law_id      TEXT NOT NULL,
    article_num TEXT NOT NULL,           -- 原始条号，与 JSON 搜索保持一致
    content TEXT NOT NULL,
    hash TEXT NOT NULL,
    PRIMARY KEY (law_id, article_num),
    FOREIGN KEY (law_id) REFERENCES laws(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_articles_law ON articles(law_id);

-- 全文搜索 (FTS5)
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    article_num, content,
    content='articles', content_rowid='rowid', tokenize='trigram case_sensitive 1'
);

-- 原始 JSON 路径 (W12 — SQLite + JSON 双层)
CREATE TABLE IF NOT EXISTS statutes_meta (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    json_path TEXT NOT NULL,             -- 相对 data/ 目录
    size_bytes INTEGER,
    content_hash TEXT,                   -- SHA256
    synced_at TEXT
);
"""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ensure_fts_sync(conn: sqlite3.Connection) -> None:
    """FTS5 external content 需手动同步"""
    conn.execute(
        "INSERT INTO articles_fts(rowid, article_num, content) "
        "SELECT rowid, article_num, content FROM articles"
    )


def build_sqlite(data_dir: Path, db_path: Path, verbose: bool = True) -> tuple[int, int]:
    """Strictly build a temporary DB, then atomically publish a verified generation."""
    if verify(data_dir) != 0:
        raise ValueError("dataset validation failed; existing database preserved")
    fingerprint = dataset_fingerprint(data_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".law-build-", suffix=".db", dir=db_path.parent)
    os.close(fd)
    staged = Path(name)
    try:
        with closing(sqlite3.connect(staged)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            conn.execute("CREATE TABLE build_metadata (fingerprint TEXT NOT NULL, complete INTEGER NOT NULL)")
            laws_count = arts_count = 0
            for path in sorted((data_dir / "statutes").glob("*.json")):
                data = json.loads(path.read_text())
                if data.get("content_hash") != content_hash(data):
                    raise ValueError(f"source changed during build: {path.name}")
                if data.get("retrieval_status") == "quarantined":
                    continue
                slug = data["slug"]
                source = data.get("source", {})
                # Numeric aliases can collide on malformed upstream headings.
                # Search the lossless primary map, exactly like the JSON fallback.
                articles = data["articles"]
                conn.execute("INSERT INTO laws VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (slug, slug, data["name"], data.get("type", ""), data.get("status", "unknown"),
                              source.get("via", ""), source.get("url", ""), source.get("fetched_at", ""),
                              source.get("updated_at", ""), len(articles)))
                rows = [(slug, str(number), text, _sha256(text)) for number, text in articles.items()]
                conn.executemany("INSERT INTO articles VALUES (?, ?, ?, ?)", rows)
                conn.execute("INSERT INTO statutes_meta VALUES (?, ?, ?, ?, ?, datetime('now'))",
                             (slug, data["name"], f"statutes/{path.name}", path.stat().st_size, data["content_hash"]))
                laws_count += 1
                arts_count += len(rows)
            _ensure_fts_sync(conn)
            conn.execute("INSERT INTO articles_fts(articles_fts, rank) VALUES ('integrity-check', 1)")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("database integrity check failed")
            if conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0] != laws_count or conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] != arts_count:
                raise ValueError("database row count mismatch")
            if dataset_fingerprint(data_dir) != fingerprint:
                raise ValueError("dataset changed during build")
            conn.execute("INSERT INTO build_metadata VALUES (?, 1)", (fingerprint,))
            conn.commit()
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(staged, db_path)
        if verbose:
            print(json.dumps({"event": "database_published", "laws": laws_count, "articles": arts_count}))
        return laws_count, arts_count
    finally:
        staged.unlink(missing_ok=True)


def verify_db(db_path: Path) -> bool:
    """校验数据库完整性"""
    if not db_path.exists():
        print(f"❌ {db_path} 不存在")
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("PRAGMA integrity_check")
        result = cur.fetchone()[0]
        if result != "ok":
            print(f"❌ integrity_check 失败: {result}")
            return False
        cur = conn.execute("SELECT COUNT(*) FROM laws")
        n_laws = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM articles")
        n_arts = cur.fetchone()[0]
        # FTS 一致性 (articles 数 == fts 行数)
        cur = conn.execute("SELECT COUNT(*) FROM articles_fts")
        n_fts = cur.fetchone()[0]
        if n_arts != n_fts:
            print(f"⚠ FTS 不一致: articles={n_arts}, fts={n_fts}")
            return False
        print(f"✅ DB 完整性: laws={n_laws}, articles={n_arts}")
        return True
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="prc-law-data SQLite 构建器 (W12)")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA),
                        help="data 目录 (默认 ./data)")
    parser.add_argument("-o", "--output", default=None,
                        help="输出 db 路径 (默认 data/prc-law.db)")
    parser.add_argument("--verify", action="store_true",
                        help="构建后跑完整性校验")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = Path(args.output) if args.output else data_dir / "prc-law.db"

    n_laws, n_arts = build_sqlite(data_dir, db_path)

    if args.verify:
        if not verify_db(db_path):
            sys.exit(1)


if __name__ == "__main__":
    main()
