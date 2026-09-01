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
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data"
STATUTES_DIR = DEFAULT_DATA / "statutes"
INDEX_DIR = DEFAULT_DATA / "index"


class Handler(http.server.BaseHTTPRequestHandler):
    data_dir: Path = DEFAULT_DATA

    def log_message(self, fmt, *args):
        # 静默 (按 token economy 规则)
        pass

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(payload)

    def _read_statute(self, slug: str) -> dict | None:
        path = self.data_dir / "statutes" / f"{slug}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_index(self) -> list[dict]:
        idx = self.data_dir / "index" / "laws.jsonl"
        if not idx.exists():
            return []
        return [json.loads(line) for line in idx.read_text(encoding="utf-8").splitlines() if line]

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)

        if path == "/healthz":
            return self._json(200, {"ok": True, "data_dir": str(self.data_dir)})

        if path == "/v1/laws":
            limit = int(qs.get("limit", [100])[0])
            offset = int(qs.get("offset", [0])[0])
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
            meta = {k: v for k, v in data.items() if k != "articles" and k != "articles_by_int"}
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
            art_num = parts[5]
            data = self._read_statute(slug)
            if not data:
                return self._json(404, {"error": "law not found", "slug": slug})
            # 优先查 articles_by_int (阿拉伯键)
            arts = data.get("articles_by_int", {})
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
            limit = int(qs.get("limit", [10])[0])
            if not q:
                return self._json(400, {"error": "missing q"})
            results = []
            for law_meta in self._read_index()[:200]:  # 限制搜索范围
                slug = law_meta["id"]
                data = self._read_statute(slug)
                if not data:
                    continue
                for art_num, text in data.get("articles", {}).items():
                    if q in text:
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    Handler.data_dir = Path(args.data_dir)
    server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"prc-law-data API server listening on http://{args.host}:{args.port}")
    print(f"  data dir: {Handler.data_dir}")
    print(f"  statutes: {len(list((Handler.data_dir / 'statutes').glob('*.json')))} files")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down...")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())