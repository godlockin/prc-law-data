"""Search only databases built from the currently published index generation."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from data_store import dataset_fingerprint


def search_database(data_dir: Path, query: str, limit: int, offset: int) -> list[dict] | None:
    database = data_dir / "prc-law.db"
    if not database.exists():
        return None
    try:
        fingerprint = dataset_fingerprint(data_dir)
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            metadata = connection.execute("SELECT fingerprint, complete FROM build_metadata").fetchall()
            if metadata != [(fingerprint, 1)]:
                return None
            sql = "SELECT l.name, l.slug, a.article_num, a.content FROM articles a JOIN laws l ON l.id=a.law_id "
            # FTS generates candidates; instr keeps exact case-sensitive substring semantics.
            if len(query) >= 3 and "\x00" not in query:
                sql += "JOIN articles_fts f ON f.rowid=a.rowid WHERE articles_fts MATCH ? AND instr(a.content, ?) > 0 "
                params = ('"' + query.replace('"', '""') + '"', query, limit, offset)
            else:
                sql += "WHERE instr(a.content, ?) > 0 "
                params = (query, limit, offset)
            rows = connection.execute(sql + "ORDER BY l.slug, a.article_num LIMIT ? OFFSET ?", params).fetchall()
            if dataset_fingerprint(data_dir) != fingerprint:
                return None
            return [{"law": row[0], "slug": row[1], "article": row[2], "snippet": row[3][:200]} for row in rows]
    except (OSError, sqlite3.Error):
        return None
