"""Compare indexed search against exact SQL scans on the same published dataset."""
from __future__ import annotations

import argparse
import json
import resource
import sqlite3
import statistics
import time
from contextlib import closing
from pathlib import Path
from search_store import search_database


def benchmark(data_dir: Path, repeats: int = 3) -> dict:
    results = []
    with closing(sqlite3.connect((data_dir / "prc-law.db").resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        for query in ("不存在的审计检索词XYZ987", "违约责任", "合同"):
            scan_times, indexed_times = [], []
            for _ in range(repeats):
                start = time.perf_counter()
                rows = connection.execute(
                    "SELECT l.name,l.slug,a.article_num,a.content FROM articles a JOIN laws l ON l.id=a.law_id "
                    "WHERE instr(a.content,?)>0 ORDER BY l.slug,a.article_num LIMIT 10", (query,)).fetchall()
                scan_times.append(time.perf_counter() - start)
                expected = [{"law": r[0], "slug": r[1], "article": r[2], "snippet": r[3][:200]} for r in rows]
                start = time.perf_counter()
                actual = search_database(data_dir, query, 10, 0)
                indexed_times.append(time.perf_counter() - start)
                if actual != expected:
                    raise ValueError(f"search result mismatch: {query}")
            results.append({"query": query, "scan_median_seconds": round(statistics.median(scan_times), 4),
                            "indexed_median_seconds": round(statistics.median(indexed_times), 4),
                            "results_equal": True, "index_used": len(query) >= 3})
    return {"repeats": repeats, "queries": results,
            "max_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "note": "macOS RSS in bytes; Linux in KiB. Sequential warm-cache local samples, not load testing."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(benchmark(args.data_dir), ensure_ascii=False, indent=2))
