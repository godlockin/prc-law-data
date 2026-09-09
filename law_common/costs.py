"""Measured usage plus evidenced prices. Unknown money is never treated as zero."""
from __future__ import annotations
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from contextlib import closing


class CostLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ValueError("ledger must not be a symlink")
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY, service_id TEXT, provider TEXT, operation TEXT, "
                         "outcome TEXT, elapsed_ms REAL, at TEXT, price_cny TEXT, evidence TEXT, price_kind TEXT)")
            conn.commit()
        os.chmod(path, 0o600)

    def start(self, service_id: str, provider: str, operation: str, tariff: dict | None = None) -> str:
        price, evidence, kind = None, None, "unknown"
        if tariff:
            try:
                amount = Decimal(str(tariff["cny_per_call"]))
            except InvalidOperation as exc:
                raise ValueError("invalid tariff") from exc
            price = str(amount)
            if not amount.is_finite() or amount < 0 or not isinstance(tariff.get("evidence"), str) or not tariff["evidence"].strip():
                raise ValueError("nonnegative price and evidence required")
            evidence, kind = tariff["evidence"], "estimate_from_tariff"
        identifier = uuid.uuid4().hex
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("INSERT INTO usage VALUES (?, ?, ?, ?, 'attempted', NULL, ?, ?, ?, ?)",
                         (identifier, service_id, provider, operation, datetime.now(timezone.utc).isoformat(), price, evidence, kind))
            conn.commit()
        return identifier

    def finish(self, identifier: str, outcome: str, elapsed_ms: float):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE usage SET outcome=?,elapsed_ms=? WHERE id=?", (outcome, elapsed_ms, identifier)); conn.commit()

    def report(self) -> dict:
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute("SELECT service_id,provider,outcome,price_cny FROM usage").fetchall()
        services = {r[0] for r in rows}; unknown = sum(r[3] is None for r in rows)
        known = sum((Decimal(r[3]) for r in rows if r[3] is not None), Decimal(0))
        return {"services": len(services), "attempts": len(rows), "unpriced_attempts": unknown,
                "priced_subtotal_cny": str(known), "total_api_cost_cny": None if unknown or not rows else str(known),
                "api_cost_per_service_cny": str(known / len(services)) if services and not unknown else None,
                "cost_kind": "tariff_estimate_not_invoice", "full_service_cost_cny": None,
                "coverage": "instrumented_router_only_not_all_account_usage",
                "excluded_costs": ["model_tokens", "human_review", "hosting", "support", "taxes"],
                "billing_note": "Attempts and failures may be billable; account invoice reconciliation remains required."}
