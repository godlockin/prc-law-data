"""Reconcile local indexes, retaining backups and explicit hash provenance."""
from __future__ import annotations
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from data_store import atomic_write, content_hash, rebuild_indexes
from verify import verify


def repair(data_dir: Path, backup_dir: Path) -> int:
    if backup_dir.exists():
        raise ValueError("backup directory must not already exist")
    pending = []
    for path in (data_dir / "statutes").glob("*.json"):
        data = json.loads(path.read_text())
        stored = data.get("content_hash")
        if stored and stored != content_hash(data):
            raise ValueError(f"existing hash mismatch; refusing to rebaseline {path.name}")
        if not stored:
            pending.append((path, data))
    backup_dir.mkdir(parents=True)
    if (data_dir / "index").exists():
        shutil.copytree(data_dir / "index", backup_dir / "index")
    for path, data in pending:
        shutil.copy2(path, backup_dir / path.name)
        data["integrity_provenance"] = {
            "status": "local_baseline_only",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "note": "Original hash absent. Local checksum does not verify source text or legal validity.",
        }
        data["content_hash"] = content_hash(data)
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
    count = rebuild_indexes(data_dir)
    atomic_write(backup_dir / "receipt.json", json.dumps({"indexed_files": count,
                 "missing_hash_baselines": [path.name for path, _ in pending],
                 "source_text_rebuilt": False}, indent=2))
    return verify(data_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(repair(args.data_dir, args.backup_dir))
