#!/usr/bin/env python3
"""
serve.py — prc-law-data HTTP 检索 API

启动本地服务, 让 PRC-Law 可远程调用 (按需加载).

用法:
    python3 serve.py --port 8765
    python3 serve.py --port 8765 --data-dir /path/to/data

Endpoints:
    GET /v1/laws                            列出所有法律 (分页: ?limit=100&offset=0)
    GET /v1/laws/<slug>                     获取单部法律元数据 + 法条统计
    GET /v1/statute/<slug>                  获取单部法律全文
    GET /v1/statute/<slug>/article/<n>      获取指定条 (阿拉伯数字)
    GET /v1/search?q=<query>&limit=10       全文搜索 (法条内容)
    GET /healthz                            健康检查
"""
from __future__ import annotations

import argparse
import http.server
import json
import sys
import os
import hmac
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from data_store import statute_path, choose_version
from search_store import search_database
import sqlite3
from service_runtime import BoundedHTTPServer, readiness, validate_config
from verify import verify
from law_common.config import settings
from law_common.access import AccessPolicy

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data"
STATUTES_DIR = DEFAULT_DATA / "statutes"
INDEX_DIR = DEFAULT_DATA / "index"


class Handler(http.server.BaseHTTPRequestHandler):
    data_dir: Path = DEFAULT_DATA
    access_token: str = ""
    require_db: bool = False

    def log_message(self, fmt, *args):
        # 静默 (按 token economy 规则)
        pass

    def _json(self, status: int, body: dict) -> None:
        self.response_status = status
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", getattr(self, "request_id", ""))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _read_statute(self, slug: str) -> dict | None:
        try:
            path = statute_path(self.data_dir, slug)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and value.get("retrieval_status") != "quarantined" else None
        except Exception:
            return None

    def _read_index(self) -> list[dict]:
        idx = self.data_dir / "index" / "laws.jsonl"
        if not idx.exists():
            return []
        return [json.loads(line) for line in idx.read_text(encoding="utf-8").splitlines() if line]

    def do_GET(self):  # noqa: N802
        start = time.monotonic()
        self.request_id = uuid.uuid4().hex
        self.response_status = 500
        try:
            if len(self.path) > 8192:
                return self._json(414, {"error": "request target too long"})
            if urlparse(self.path).path != "/healthz" and self.access_token:
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.access_token).encode()):
                    return self._json(401, {"error": "unauthorized"})
                policy = getattr(getattr(self, "server", None), "access_policy", None)
                if policy is not None and not policy.allowed("dataset"):
                    return self._json(429, {"error": "rate limit exceeded"})
            return self._get()
        except (ValueError, TypeError):
            return self._json(400, {"error": "invalid request parameters or data"})
        except (OSError, KeyError, sqlite3.Error):
            return self._json(503, {"error": "dataset unavailable"})
        finally:
            metrics = getattr(getattr(self, "server", None), "metrics", None)
            if metrics is not None:
                elapsed = time.monotonic() - start
                metrics.record(self.response_status, elapsed)
                # Never log query strings, law names, tokens, client addresses or raw paths.
                print(json.dumps({"event": "http_request", "request_id": self.request_id,
                                  "status": self.response_status, "duration_ms": round(elapsed * 1000, 2)}),
                      file=sys.stderr, flush=True)

    def _get(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        qs = parse_qs(url.query)
        limit = int(qs.get("limit", [100 if path == "/v1/laws" else 10])[0])
        offset = int(qs.get("offset", [0])[0])
        if not 1 <= limit <= 1000 or not 0 <= offset <= 10000:
            return self._json(400, {"error": "limit must be 1..1000; offset must be 0..10000"})

        if path == "/v1/resolve":
            law = qs.get("law", [""])[0]
            mapping = json.loads((self.data_dir / "index/slug-map.json").read_text())
            base = mapping.get(law, law)
            version_file = self.data_dir / "index/versions.json"
            versions = json.loads(version_file.read_text()) if version_file.exists() else {}
            candidates = [self._read_statute(slug) for slug in versions.get(base, [base])]
            selected = choose_version([item for item in candidates if item], qs.get("as_of", [None])[0], qs.get("article", [None])[0])
            if not selected:
                return self._json(404, {"error": "no unambiguous version"})
            return self._json(200, {"slug": selected["slug"], "effective_date": selected.get("effective_date", ""),
                                    "label": "单源—需复核", "version_conflicts": selected.get("version_conflicts", False)})

        if path == "/healthz":
            return self._json(200, {"ok": True})

        if path == "/readyz":
            state = readiness(self.data_dir, self.require_db)
            return self._json(200 if state["ready"] else 503, state)

        if path == "/metrics":
            return self._json(200, self.server.metrics.snapshot())

        if path == "/v1/laws":
            items = self._read_index()
            return self._json(200, {
                "total": len(items),
                "limit": limit,
                "offset": offset,
                "items": items[offset:offset + limit],
            })

        # /v1/laws/<slug>  (元数据,不含全文)
        if path.startswith("/v1/laws/"):
            slug = path[len("/v1/laws/"):]
            data = self._read_statute(slug)
            if not data:
                return self._json(404, {"error": "law not found", "slug": slug})
            meta = {k: v for k, v in data.items() if k not in ("articles", "articles_by_int", "raw_content")}
            meta["article_count"] = data.get("article_count", len(data.get("articles", {})))
            return self._json(200, meta)

        # /v1/statute/<slug>
        if path.startswith("/v1/statute/") and path.count("/") == 3:
            slug = path[len("/v1/statute/"):]
            data = self._read_statute(slug)
            if not data:
                return self._json(404, {"error": "law not found", "slug": slug})
            return self._json(200, data)

        # /v1/statute/<slug>/article/<n>
        if path.startswith("/v1/statute/") and path.count("/") == 5:
            parts = path.split("/")
            # ['v1', 'statute', '<slug>', 'article', '<n>']
            slug = parts[3]
            if parts[4] != "article":
                return self._json(404, {"error": "not found"})
            art_num = parts[5]
            data = self._read_statute(slug)
            if not data:
                return self._json(404, {"error": "law not found", "slug": slug})
            # 优先查 articles_by_int (阿拉伯键)
            arts = data.get("articles_by_int", {})
            if arts and len(arts) != len(data.get("articles", {})):
                return self._json(409, {"error": "ambiguous article numbering; review original source"})
            text = arts.get(art_num)
            if not text:
                text = data.get("articles", {}).get(art_num)
            if not text:
                return self._json(404, {
                    "error": "article not found",
                    "slug": slug,
                    "article": art_num,
                    "available_articles": list(arts.keys())[:5] + ["..."],
                })
            return self._json(200, {
                "law": data["name"],
                "slug": slug,
                "article": art_num,
                "content": text,
                "source": data["source"]["via"],
                "fetched_at": data["source"]["fetched_at"],
            })

        # /v1/search?q=<query>
        if path == "/v1/search":
            q = qs.get("q", [""])[0].strip()
            if not q:
                return self._json(400, {"error": "missing q"})
            if len(q) > 256:
                return self._json(400, {"error": "query must be at most 256 characters"})
            results = []
            database_results = search_database(self.data_dir, q, limit, offset)
            if database_results is not None:
                return self._json(200, {"q": q, "count": len(database_results), "results": database_results})
            if self.require_db:
                return self._json(503, {"error": "search database unavailable"})
            skipped = 0
            for law_meta in self._read_index():  # 全量召回，禁止静默截断法律范围
                slug = law_meta["id"]
                data = self._read_statute(slug)
                if not data:
                    continue
                for art_num, text in data.get("articles", {}).items():
                    if q in text:
                        if skipped < offset:
                            skipped += 1
                            continue
                        results.append({
                            "law": data["name"],
                            "slug": slug,
                            "article": art_num,
                            "snippet": text[:200],
                        })
                        if len(results) >= limit:
                            break
                if len(results) >= limit:
                    break
            return self._json(200, {"q": q, "count": len(results), "results": results})

        return self._json(404, {"error": "not found", "path": path})


def main() -> int:
    config = settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=config["service_port"])
    parser.add_argument("--data-dir", default=config["dataset_dir"] or str(DEFAULT_DATA))
    parser.add_argument("--host", default=config["service_host"])
    parser.add_argument("--workers", type=int, default=config["workers"])
    parser.add_argument("--require-db", action="store_true", help="Fail readiness/search if the indexed database is unavailable.")
    args = parser.parse_args()

    token = os.environ.get("PRC_LAW_DATA_TOKEN", "")
    try:
        validate_config(args.host, args.port, args.workers, token)
    except ValueError as exc:
        parser.error(str(exc))
    class ConfiguredHandler(Handler):
        data_dir = Path(args.data_dir)
        access_token = token
        require_db = args.require_db
    if verify(ConfiguredHandler.data_dir) != 0 or not readiness(ConfiguredHandler.data_dir, args.require_db)["ready"]:
        parser.error("dataset not ready; refusing startup")
    server = BoundedHTTPServer((args.host, args.port), ConfiguredHandler, workers=args.workers)
    server.access_policy = AccessPolicy({"dataset": {"token": token, "role": "user"}}, config["requests_per_minute"]) if token else None
    print(json.dumps({"event": "server_started", "port": server.server_port, "workers": args.workers}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
