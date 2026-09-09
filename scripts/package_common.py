"""Reproducible, offline wheel distribution of the canonical shared code."""
import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile


def package(destination: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    entries = {str(p.relative_to(root)): p.read_bytes() for p in (root / "law_common").glob("*.py")}
    metadata = "prc_law_common-1.0.0.dist-info"
    entries[metadata + "/METADATA"] = b"Metadata-Version: 2.1\nName: prc-law-common\nVersion: 1.0.0\nRequires-Python: >=3.10\nRequires-Dist: cryptography==50.0.0\n"
    entries[metadata + "/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: local\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    rows = io.StringIO(); writer = csv.writer(rows, lineterminator="\n")
    for name, data in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + digest, len(data)))
    writer.writerow((metadata + "/RECORD", "", "")); entries[metadata + "/RECORD"] = rows.getvalue().encode()
    destination.mkdir(parents=True, exist_ok=True)
    wheel = destination / "prc_law_common-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data)
    (destination / "common-package.json").write_text(json.dumps({"file": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--destination", type=Path, required=True)
    package(parser.parse_args().destination)
