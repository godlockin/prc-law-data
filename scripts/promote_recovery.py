"""Publish an offline-validated recovery with directory backups and rollback."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from data_store import atomic_write
from verify import verify


def promote(staged: Path, target: Path, backup: Path, validator: Callable[[Path], int] = verify,
            writer: Callable[[Path, str], None] = atomic_write) -> None:
    staged, target, backup = staged.resolve(), target.resolve(), backup.resolve()
    pairs = ((staged, target), (staged, backup), (target, backup))
    if backup.exists() or any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for a, b in pairs):
        raise ValueError("staging and backup must be separate; backup must not exist")
    if validator(staged) != 0:
        raise ValueError("staging validation failed")
    report = (staged / "recovery-report.json").read_text()
    json.loads(report)
    old_report = target / "recovery-report.json"
    backup.mkdir(parents=True)
    saved, installed = [], []
    report_saved = False
    try:
        if old_report.exists():
            old_report.rename(backup / "recovery-report.json")
            report_saved = True
        for name in ("statutes", "index"):
            (target / name).rename(backup / name)
            saved.append(name)
            (staged / name).rename(target / name)
            installed.append(name)
        if validator(target) != 0:
            raise ValueError("published dataset validation failed")
        writer(target / "recovery-report.json", report)
        writer(backup / "publication-receipt.json", json.dumps({
            "published_at": datetime.now(timezone.utc).isoformat(), "target": str(target),
            "backup": str(backup), "validated": True,
            "note": "Snapshot/text integrity checked; not an official legal-validity certification."}, indent=2))
    except BaseException:
        for name in reversed(installed):
            (target / name).rename(staged / name)
        for name in reversed(saved):
            (backup / name).rename(target / name)
        if report_saved:
            (backup / "recovery-report.json").replace(old_report)
        else:
            old_report.unlink(missing_ok=True)
        (backup / "publication-receipt.json").unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pause dataset services before publication.")
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    promote(args.staged, args.target, args.backup)
