"""Evidence-bound official page review. Page freshness is not legal applicability."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from .rules import law_id

HOSTS = {"www.gov.cn", "www.npc.gov.cn", "flk.npc.gov.cn"}


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ValueError("only explicitly allowed official HTTPS hosts are accepted")


def fetch(url: str) -> bytes:
    validate_url(url)
    for address in socket.getaddrinfo(urlsplit(url).hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("non-public official endpoint address")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # Changed official locations require explicit watchlist review.
    request = urllib.request.Request(url, headers={"User-Agent": "PRC-Law-Evidence-Checker/1.0", "Accept": "text/html,application/json"})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        raw = response.read(2_000_001)
    if not raw or len(raw) > 2_000_000:
        raise ValueError("empty or oversized official response")
    return raw


def write_json(path: Path, value: dict) -> None:
    import os, tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        json.dump(value, output, ensure_ascii=False, indent=2); name = output.name
    try:
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _check(root: Path, entry: dict, *, fetcher=fetch, now=None) -> dict:
    url, name = entry["url"], entry["law"]
    validate_url(url)
    identifier = hashlib.sha256((law_id(name) + "\n" + url).encode()).hexdigest()
    record = root / "records" / (identifier + ".json")
    previous = json.loads(record.read_text()) if record.exists() else {}
    now = now or datetime.now(timezone.utc)
    result = {**previous, "id": identifier, "law": name, "url": url, "last_attempt_at": now.isoformat(),
              "scope": "listed_page_only_not_complete_amendment_discovery"}
    try:
        raw = fetcher(url)
        if not raw or len(raw) > 2_000_000:
            raise ValueError("empty or oversized response")
        digest = hashlib.sha256(raw).hexdigest()
        snapshot = root / "snapshots" / (digest + ".bin")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(raw)
        result.update(observed_sha256=digest, checked_at=now.isoformat(),
                      status="reviewed_page_unchanged" if previous.get("approved_sha256") == digest else "pending_review")
        result.pop("error_type", None)
        result.pop("http_status", None)
    except (OSError, ValueError) as exc:
        result.update(status="check_failed", error_type=type(exc).__name__)
        if isinstance(getattr(exc, "code", None), int):
            result["http_status"] = exc.code
    write_json(record, result)
    return result


def _approve(root: Path, identifier: str, digest: str, reviewer: str, scope: str, *, now=None) -> dict:
    if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier) or not reviewer.strip() or not scope.strip():
        raise ValueError("record ID, named reviewer and validity review scope required")
    path = root / "records" / (identifier + ".json")
    data = json.loads(path.read_text())
    snapshot = root / "snapshots" / (digest + ".bin")
    if data.get("observed_sha256") != digest or data.get("status") == "check_failed" or hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
        raise ValueError("review must bind to the currently observed snapshot")
    data.update(approved_sha256=digest, reviewer=reviewer, validity_review_scope=scope,
                reviewed_at=(now or datetime.now(timezone.utc)).isoformat(), status="reviewed_page_unchanged")
    write_json(path, data)
    return data


def check(root: Path, entry: dict, *, fetcher=fetch, now=None) -> dict:
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".review.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _check(root, entry, fetcher=fetcher, now=now)


def approve(root: Path, identifier: str, digest: str, reviewer: str, scope: str, *, now=None) -> dict:
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".review.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _approve(root, identifier, digest, reviewer, scope, now=now)


def status(root: Path, law: str, max_age_days: int, *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    records = []
    for path in (root / "records").glob("*.json"):
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("invalid evidence record")
        except (OSError, ValueError):
            return {"status": "verification_unavailable", "records": [], "official_current_validity_proven": False,
                    "explanation": "核验记录不可读，需要维护人员处理；不能认定为已核验。"}
        if law_id(data.get("law")) != law_id(law):
            continue
        state = data.get("status", "not_checked")
        try:
            age = (now - datetime.fromisoformat(data["checked_at"])).total_seconds()
            if age < 0 or age > max_age_days * 86400:
                state = "stale"
        except (KeyError, ValueError, TypeError):
            state = "not_checked" if state != "check_failed" else state
        records.append({"id": data["id"], "url": data["url"], "status": state,
                        "checked_at": data.get("checked_at"), "reviewer": data.get("reviewer")})
    return {"status": "not_checked" if not records else "review_required" if any(r["status"] != "reviewed_page_unchanged" for r in records) else "listed_pages_reviewed",
            "records": records, "official_current_validity_proven": False,
            "explanation": "仅核对列明官方页面及具名复核记录；不证明已覆盖全部后续修法或适用于具体案件。"}
