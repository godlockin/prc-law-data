"""Dataset readiness; shared HTTP runtime is maintained in law_common."""
import sqlite3
from pathlib import Path
from contextlib import closing
from data_store import dataset_fingerprint
from law_common.http_support import BoundedHTTPServer, Metrics, validate_config

def readiness(data_dir: Path, require_db: bool = False) -> dict:
    try:
        fingerprint = dataset_fingerprint(data_dir)
        if not (data_dir / "statutes").is_dir() or not (data_dir / "index/laws.jsonl").stat().st_size:
            raise ValueError("missing dataset")
        database_ready = False
        database = data_dir / "prc-law.db"
        if database.exists():
            try:
                with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                    database_ready = conn.execute("SELECT fingerprint, complete FROM build_metadata").fetchall() == [(fingerprint, 1)]
            except sqlite3.Error:
                pass
        return {"ready": database_ready or not require_db, "database_ready": database_ready,
                "mode": "sqlite" if database_ready else "json", "generation": fingerprint}
    except (OSError, ValueError):
        return {"ready": False, "database_ready": False, "reason": "dataset_unavailable"}
