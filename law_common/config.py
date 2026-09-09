"""One non-secret runtime configuration, with explicit environment overrides."""
from __future__ import annotations
import json
import os
from pathlib import Path

DEFAULTS = {
    "service_host": "127.0.0.1", "service_port": 8765, "workers": 16,
    "requests_per_minute": 120, "retention_days": 30, "official_max_age_days": 7,
    "yuandian_quota": 5000, "pkulaw_quota": 5000,
    "dataset_dir": "", "dataset_url": "", "state_dir": "",
}
ENV = {"dataset_dir": "PRC_LAW_DATA_DIR", "dataset_url": "PRC_LAW_DATA_URL",
       "state_dir": "PRC_LAW_STATE_DIR", "yuandian_quota": "PRC_LAW_YUANDIAN_QUOTA",
       "pkulaw_quota": "PRC_LAW_PKULAW_QUOTA"}


def settings() -> dict:
    values = dict(DEFAULTS)
    path = os.environ.get("PRC_LAW_CONFIG")
    if path:
        supplied = json.loads(Path(path).expanduser().read_text())
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
            raise ValueError("unknown configuration keys; credentials belong in environment variables")
        values.update(supplied)
    for key, name in ENV.items():
        if name in os.environ:
            values[key] = int(os.environ[name]) if isinstance(DEFAULTS[key], int) else os.environ[name]
    for key, default in DEFAULTS.items():
        if type(values[key]) is not type(default):
            raise ValueError(f"invalid configuration type: {key}")
    for key in ("workers", "requests_per_minute", "retention_days", "official_max_age_days"):
        if not 1 <= values[key] <= (128 if key == "workers" else 10000):
            raise ValueError(f"invalid configuration range: {key}")
    if not 0 <= values["service_port"] <= 65535 or min(values["yuandian_quota"], values["pkulaw_quota"]) < 0:
        raise ValueError("invalid port or quota")
    return values


def state_dir() -> Path:
    return Path(settings()["state_dir"] or Path.home() / ".local/share/prc-law").expanduser().resolve()
