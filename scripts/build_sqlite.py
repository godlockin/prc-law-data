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
    article_num TEXT NOT NULL,           -- 阿拉伯数字字符串
    content TEXT NOT NULL,
    hash TEXT NOT NULL,
    PRIMARY KEY (law_id, article_num),
    FOREIGN KEY (law_id) REFERENCES laws(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_articles_law ON articles(law_id);

-- 全文搜索 (FTS5)
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    article_num, content,
    content='articles', content_rowid='rowid'
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


def build_sqlite(data_dir: Path, db_path: Path, verbose: bool = True) -> int:
    """构建 SQLite 数据库

    Returns:
        写入 laws/articles 数 (元组)
    """
    statutes_dir = data_dir / "statutes"
    index_path = data_dir / "index" / "laws.jsonl"

    if not statutes_dir.exists():
        print(f"❌ {statutes_dir} 不存在", file=sys.stderr)
        sys.exit(2)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 删除旧 db, 重建 (幂等)
    if db_path.exists():
        if verbose:
            print(f"⚠ 删除旧 db: {db_path}")
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    # FTS5 external content sync triggers
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, article_num, content)
            VALUES (new.rowid, new.article_num, new.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, article_num, content)
            VALUES ('delete', old.rowid, old.article_num, old.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, article_num, content)
            VALUES ('delete', old.rowid, old.article_num, old.content);
            INSERT INTO articles_fts(rowid, article_num, content)
            VALUES (new.rowid, new.article_num, new.content);
        END
    """)

    laws_count = 0
    arts_count = 0

    # 1. 优先从 laws.jsonl 索引读 (含 type/ status/ source 信息)
    law_index: dict[str, dict] = {}
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                law_index[e["slug"]] = e
            except Exception:
                continue

    # 2. 遍历 statutes/*.json
    for path in sorted(statutes_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            if verbose:
                print(f"  ⚠ 跳过 {path.name}: {e}", file=sys.stderr)
            continue

        slug = data.get("slug") or path.stem
        name = data.get("name", slug)
        articles_by_int = data.get("articles_by_int", {})
        article_count = data.get("article_count", len(articles_by_int))
        source = data.get("source", {})
        idx_entry = law_index.get(slug, {})

        law_id = idx_entry.get("id") or f"law-{slug}"

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO laws (
                    id, slug, name, type, status,
                    source_via, source_url, fetched_at, updated_at, article_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    law_id,
                    slug,
                    name,
                    idx_entry.get("type") or data.get("type", "法律"),
                    idx_entry.get("status") or data.get("status", "effective"),
                    idx_entry.get("source_via") or source.get("via", ""),
                    idx_entry.get("source_url") or source.get("url", ""),
                    idx_entry.get("fetched_at") or source.get("fetched_at", ""),
                    idx_entry.get("updated_at") or source.get("updated_at", ""),
                    article_count,
                ),
            )
            laws_count += 1
        except sqlite3.IntegrityError as e:
            if verbose:
                print(f"  ⚠ law 插入失败 {slug}: {e}", file=sys.stderr)
            continue

        # 3. 插入 articles (批量)
        rows = []
        for art_num, text in articles_by_int.items():
            rows.append((
                law_id,
                str(art_num),
                text,
                _sha256(text),
            ))
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO articles (law_id, article_num, content, hash) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            arts_count += len(rows)
        except sqlite3.IntegrityError as e:
            if verbose:
                print(f"  ⚠ articles 插入失败 {slug}: {e}", file=sys.stderr)

        # 4. statutes_meta
        size = path.stat().st_size
        meta_hash = _sha256(path.read_text(encoding="utf-8"))
        conn.execute(
                    """
                    INSERT OR REPLACE INTO statutes_meta (
                        slug, name, json_path, size_bytes, content_hash, synced_at
                    ) VALUES (?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (slug, name, f"statutes/{path.name}", size, meta_hash),
                )

    conn.commit()

    # 5. 健康检查
    cur = conn.execute("SELECT COUNT(*) FROM laws")
    n_laws = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM articles")
    n_arts = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM articles_fts")
    n_fts = cur.fetchone()[0]

    if verbose:
        print(f"✅ SQLite 构建完成: {db_path}")
        print(f"   laws: {n_laws}")
        print(f"   articles: {n_arts}")
        print(f"   articles_fts: {n_fts}")

    conn.close()
    return n_laws, n_arts


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
    parser.add_argument("-o", "--output", default=str(DEFAULT_DB),
                        help="输出 db 路径 (默认 data/prc-law.db)")
    parser.add_argument("--verify", action="store_true",
                        help="构建后跑完整性校验")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = Path(args.output)

    n_laws, n_arts = build_sqlite(data_dir, db_path)

    if args.verify:
        if not verify_db(db_path):
            sys.exit(1)


if __name__ == "__main__":
    main()