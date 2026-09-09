"""One-shot watchlist checks and hash-bound review; no scheduler or notifications."""
import argparse
import json
from pathlib import Path
import common_bootstrap
from law_common.official import check, approve

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    scan = sub.add_parser("check"); scan.add_argument("--watchlist", type=Path, required=True)
    review = sub.add_parser("approve")
    for name in ("id", "sha256", "reviewer", "scope"):
        review.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if args.action == "check":
        entries = json.loads(args.watchlist.read_text())
        if not isinstance(entries, list) or len(entries) > 100:
            parser.error("watchlist must contain at most 100 entries")
        results = [check(args.root, item) for item in entries]
        print(json.dumps({"checked": len(results), "pending_review": sum(r["status"] == "pending_review" for r in results),
                          "failed": sum(r["status"] == "check_failed" for r in results)}))
        raise SystemExit(1 if any(r["status"] == "check_failed" for r in results) else 0)
    else:
        result = approve(args.root, args.id, args.sha256, args.reviewer, args.scope)
        print(json.dumps({"id": result["id"], "status": result["status"]}))
