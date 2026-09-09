"""Local operational preflight; not a certification of legal validity."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from service_runtime import readiness


def audit(data_dir: Path) -> dict:
    counts = {"files": 0, "quarantined": 0, "lossy_article_maps": 0}
    anomalies = []
    for path in (data_dir / "statutes").glob("*.json"):
        data = json.loads(path.read_text())
        counts["files"] += 1
        if data.get("retrieval_status") == "quarantined":
            counts["quarantined"] += 1
            continue
        if data.get("articles_by_int") and len(data["articles_by_int"]) != len(data.get("articles", {})):
            counts["lossy_article_maps"] += 1
            anomalies.append(path.stem)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "technical_readiness": readiness(data_dir, True),
            "counts": counts, "numbering_review_queue": anomalies,
            "unattended_legal_opinions_ready": False,
            "unverified_release_gates": ["official_source_freshness", "expert_annotated_case_evaluation",
                                         "long_running_availability", "off_host_restore_drill",
                                         "deployment_tls_and_identity", "privacy_and_retention_review"],
            "note": "Technical readiness checks index/database generation only; full integrity requires verify.py."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit(args.data_dir), ensure_ascii=False, indent=2))
