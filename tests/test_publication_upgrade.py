import json
import os
import tempfile
import unittest
from pathlib import Path

import test_data_safety
from data_store import atomic_write, rebuild_indexes, content_hash
from build_sqlite import build_sqlite
from promote_recovery import promote
from search_store import search_database


class PublicationTests(unittest.TestCase):
    write_statute = test_data_safety.DataSafetyTests.write_statute

    def test_search_retains_original_headings_when_numeric_aliases_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_statute(root, "valid")
            path = root / "statutes/valid.json"
            data = json.loads(path.read_text())
            data.update(articles={"二十": "第一处目标", "八二十": "第二处目标"},
                        articles_by_int={"20": "第二处目标"}, article_count=2)
            data["content_hash"] = content_hash(data)
            path.write_text(json.dumps(data))
            rebuild_indexes(root)
            self.assertEqual(build_sqlite(root, root / "prc-law.db", False), (1, 2))
            self.assertEqual(len(search_database(root, "处目标", 10, 0)), 2)
            handler = test_data_safety.DataSafetyTests()
            self.assertEqual(handler.handler(root, "/v1/statute/valid/article/20")[0], 409)

    def test_corrupt_source_preserves_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_statute(root, "valid")
            rebuild_indexes(root)
            db = root / "prc-law.db"
            build_sqlite(root, db, False)
            before = db.read_bytes()
            (root / "statutes/valid.json").write_text("{broken")
            with self.assertRaises(ValueError):
                build_sqlite(root, db, False)
            self.assertEqual(before, db.read_bytes())
            self.assertFalse(list(root.glob(".law-build-*")))

    def test_receipt_failure_restores_directories_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, staged, backup = (root / n for n in ("old", "staged", "backup"))
            for directory, slug in ((old, "old"), (staged, "new")):
                directory.mkdir()
                self.write_statute(directory, slug)
                rebuild_indexes(directory)
                (directory / "recovery-report.json").write_text(json.dumps({"version": slug}))
            def writer(path, text):
                if path.name == "publication-receipt.json":
                    raise OSError("simulated receipt failure")
                atomic_write(path, text)
            with self.assertRaises(OSError):
                promote(staged, old, backup, writer=writer)
            self.assertTrue((old / "statutes/old.json").exists())
            self.assertTrue((staged / "statutes/new.json").exists())
            self.assertEqual(json.loads((old / "recovery-report.json").read_text())["version"], "old")

    def test_index_identity_and_exact_substring_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = '中文违约责任 a"b ABC %_ 标点。'
            self.write_statute(root, "valid", text)
            rebuild_indexes(root)
            build_sqlite(root, root / "prc-law.db", False)
            for query in ('违约责任', '中文', '中', 'a"b', 'ABC', 'abc', '%_', '标点。', '不存在'):
                self.assertEqual(bool(search_database(root, query, 10, 0)), query in text, query)
            self.assertEqual(search_database(root, "违约责任", 10, 1), [])
            index = root / "index/laws.jsonl"
            prior = index.stat()
            index.write_text(index.read_text() + "\n")
            os.utime(index, ns=(prior.st_atime_ns, prior.st_mtime_ns))
            self.assertIsNone(search_database(root, "违约责任", 10, 0))
