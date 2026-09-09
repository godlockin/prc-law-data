"""Build a reviewable recovery dataset from pinned archives; never infer lost text."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import re
import sqlite3
import shutil
import tempfile
import os
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from data_store import atomic_write, content_hash, rebuild_indexes

importer = importlib.import_module("import")


def identity(data: dict) -> tuple[str, str, str]:
    def day(key: str) -> str:
        value = data.get(key, "")
        return "" if value in (None, "NaT", "None", "nan", "NaN") else value
    return data["name"].removeprefix("中华人民共和国"), day("publish_date"), day("effective_date")


def recover(cache: Path, old: Path, staged: Path) -> dict:
    def check_blob(path: Path, expected: str) -> None:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"snapshot hash mismatch: {path.name}")
    for manifest_name in ("manifest.json", "hf-manifest.json", "lawrefbook-manifest.json"):
        manifest_file = cache / manifest_name
        if not manifest_file.exists():
            continue
        manifest = json.loads(manifest_file.read_text())
        if "archive" in manifest:
            check_blob(Path(manifest["archive"]), manifest["sha256"])
        for entry in manifest.get("files", []):
            check_blob(Path(entry["path"]), entry["sha256"])
    if staged.exists():
        raise ValueError("staging directory already exists")
    staged.mkdir(parents=True)
    report = {"counts": Counter(), "rejected": [], "unstructured": [], "changed": [], "unrecovered": []}
    old_by_identity = {}
    for path in (old / "statutes").glob("*.json"):
        data = json.loads(path.read_text())
        old_by_identity.setdefault(identity(data), []).append((path, data))
    recovered_keys = set()
    seen = set()

    def accept(raw: dict, provenance: dict) -> None:
        report["counts"]["source_records"] += 1
        try:
            data = importer.normalize(raw)
        except (ValueError, TypeError, KeyError) as exc:
            # Preserve authoritative snapshot bytes even when article boundaries are unsafe.
            if not raw.get("content"):
                report["rejected"].append({"name": raw.get("name"), "source": raw["_source"], "reason": str(exc)})
                return
            data = importer.normalize({**raw, "_raw": {}, "content": "", "total_articles": None})
            data["raw_content"] = raw["content"]
            data["parsing_warning"] = str(exc)
            report["unstructured"].append({"name": raw.get("name"), "source": raw["_source"], "reason": str(exc)})
        if not data or not data.get("raw_content"):
            report["rejected"].append({"name": raw.get("name"), "source": raw["_source"], "reason": "missing source text"})
            return
        base = data["slug"]
        version_material = json.dumps({"law": base, "publish": data["publish_date"], "effective": data["effective_date"],
                                       "articles": {key: re.sub(r"\s+", "", text) for key, text in data["articles_by_int"].items()},
                                       "raw": data["raw_content"] if not data["articles"] else ""},
                                      sort_keys=True, ensure_ascii=False)
        version = hashlib.sha256(version_material.encode()).hexdigest()[:16]
        slug = f"{base}--{version}"
        if slug in seen:
            return
        seen.add(slug)
        data.update({"law_id": base, "version_id": version, "id": slug, "slug": slug,
                     "retrieval_status": "recovered_from_snapshot", "authority_verified": False})
        data["source"].update(provenance)
        data["source"]["upstream"] = provenance["origin_url"]
        if not data["articles"]:
            data["parsing_status"] = "unstructured_text"
        data["content_hash"] = content_hash(data)
        atomic_write(staged / "statutes" / f"{slug}.json", json.dumps(data, ensure_ascii=False, indent=2))
        report["counts"]["recovered_versions"] += 1
        report["counts"]["recovered_articles"] += len(data["articles"])
        key = identity(data)
        recovered_keys.add(key)
        for path, previous in old_by_identity.get(key, []):
            before, after = previous.get("articles", {}), data["articles"]
            differences = sum(before.get(number) != text for number, text in after.items())
            if differences or set(before) - set(after):
                report["changed"].append({"old_file": path.name, "new_file": slug + ".json", "name": data["name"],
                                          "before_articles": len(before), "after_articles": len(after), "changed_articles": differences})

    github = json.loads((cache / "manifest.json").read_text())
    with zipfile.ZipFile(github["archive"]) as archive:
        for name in archive.namelist():
            if "/json/" not in name or not name.endswith(".json"):
                continue
            d = json.loads(archive.read(name))
            path = name.split("/", 1)[1]
            accept({"_source": "laws-data", "_raw": d, "name": d.get("title", ""),
                    "type": d.get("category", "法律"), "publish_date": d.get("pub_date", ""),
                    "effective_date": d.get("effective_date", ""), "office": d.get("issuing_org", ""),
                    "content": d.get("full_text", ""), "total_articles": d.get("total_articles"),
                    "status": "现行有效" if d.get("is_current") in (1, True) else "unknown"},
                   {"snapshot_commit": github["commit"], "snapshot_sha256": github["sha256"],
                    "origin_url": f"https://github.com/{github['repo']}/blob/{github['commit']}/{quote(path)}"})
    import pyarrow.parquet as pq
    hf = json.loads((cache / "hf-manifest.json").read_text())
    for entry in hf["files"]:
        if "/metadata/" in entry["url"]:
            continue
        for batch in pq.ParquetFile(entry["path"]).iter_batches(batch_size=128):
            for d in batch.to_pylist():
                accept({"_source": "hf", "name": d.get("title", ""), "type": d.get("type", "法律"),
                        "publish_date": str(d.get("publish_date") or "")[:10],
                        "effective_date": str(d.get("effective_date") or "")[:10],
                        "status": d.get("status", "unknown"), "office": d.get("office", ""), "content": d.get("content", "")},
                       {"snapshot_commit": hf["commit"], "snapshot_sha256": entry["sha256"], "origin_url": entry["url"]})
    lawref_file = cache / "lawrefbook-manifest.json"
    if lawref_file.exists():
        lawref = json.loads(lawref_file.read_text())
        # Derive SQLite from the verified archive rather than trusting a separate cache file.
        with zipfile.ZipFile(lawref["archive"]) as archive:
            database_members = [member for member in archive.namelist() if member.endswith("/db.sqlite3") and member.count("/") == 1]
            if len(database_members) != 1 or archive.getinfo(database_members[0]).file_size > 1024 ** 3:
                raise ValueError("missing or oversized LawRefBook root database")
            with archive.open(database_members[0]) as source, tempfile.NamedTemporaryFile(dir=cache, delete=False) as target:
                shutil.copyfileobj(source, target)
                temporary_name = target.name
            os.replace(temporary_name, cache / "lawref.sqlite3")
        with zipfile.ZipFile(lawref["archive"]) as archive, sqlite3.connect((cache / "lawref.sqlite3").resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            names = {}
            for member in archive.namelist():
                if "/DLC/" not in member and member.endswith(".md"):
                    names.setdefault(member.rsplit("/", 1)[-1], []).append(member)
            for name, filename, published, effective, expiry, level in conn.execute("SELECT name,filename,publish,valid_from,valid_to,level FROM law"):
                matches = names.get(f"{name}({published}).md", [])
                if not matches and filename:
                    matches = names.get(filename.rsplit("/", 1)[-1], [])
                if len(matches) != 1:
                    continue
                member = matches[0]
                content = archive.read(member).decode("utf-8-sig")
                end = expiry if expiry and not expiry.startswith("2099") else ""
                accept({"_source": "lawrefbook", "name": name, "type": level, "publish_date": published or "",
                        "effective_date": effective or "", "expiry_date": end,
                        "status": "已废止" if end else "现行有效", "content": content},
                       {"snapshot_commit": lawref["commit"], "snapshot_sha256": lawref["sha256"],
                        "origin_url": f"https://github.com/{lawref['repo']}/blob/{lawref['commit']}/{quote(member.split('/',1)[1])}"})
    # Retain unmatched legacy documents for audit, but exclude them from retrieval.
    for key, entries in old_by_identity.items():
        if key in recovered_keys:
            continue
        for path, data in entries:
            data["retrieval_status"] = "quarantined"
            data["quarantine_reason"] = "No matching source name/publication/effective date in recovered snapshots"
            data["content_hash"] = content_hash(data)
            atomic_write(staged / "statutes" / path.name, json.dumps(data, ensure_ascii=False, indent=2))
            report["unrecovered"].append({"file": path.name, "name": data["name"], "source": data.get("source", {}).get("via")})
    report["counts"]["unrecovered_legacy_files"] = len(report["unrecovered"])
    report["counts"]["rejected_source_records"] = len(report["rejected"])
    report["counts"]["unstructured_source_records"] = len(report["unstructured"])
    report["counts"]["indexed_files"] = rebuild_indexes(staged)
    report["counts"] = dict(report["counts"])
    atomic_write(staged / "recovery-report.json", json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--old-data", type=Path, required=True)
    parser.add_argument("--staged-data", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(recover(args.cache, args.old_data, args.staged_data)["counts"], ensure_ascii=False))
