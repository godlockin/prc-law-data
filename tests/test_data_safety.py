"""Isolated parser, HTTP handler and import regression tests."""
import contextlib
import importlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import serve
import verify
import build_sqlite
from promote_recovery import promote
from recover_snapshot import recover, identity
from data_store import content_hash, rebuild_indexes
importer = importlib.import_module("import")


class DataSafetyTests(unittest.TestCase):
    def test_recovery_rejects_changed_snapshot_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            archive = cache / "source.zip"
            archive.write_bytes(b"changed snapshot")
            (cache / "manifest.json").write_text(json.dumps({"archive": str(archive), "sha256": "0" * 64}))
            with self.assertRaisesRegex(ValueError, "snapshot hash mismatch"):
                recover(cache, root / "old", root / "staged")
            self.assertFalse((root / "staged").exists())
        self.assertEqual(identity({"name": "中华人民共和国测试规则", "publish_date": "NaT"}),
                         identity({"name": "测试规则", "publish_date": ""}))

    def test_parser_keeps_citations_long_text_and_inserted_articles(self):
        first = "依照第二条规定办理后续事项。" + "长条文" * 900
        parsed = importer._parse_articles(f"第一条 {first}\n第二条 正文。\n第二条之一 增补正文。")
        self.assertEqual(parsed["一"], first)
        self.assertEqual(parsed["二之一"], "增补正文。")
        with self.assertRaises(ValueError):
            importer._parse_articles("第一条 前文\n第一条 后文")

    def test_structured_source_splits_inserted_articles_and_strips_chapter_titles(self):
        data = importer.normalize({"name": "测试规则", "_source": "test", "total_articles": 1,
                                   "_raw": {"articles": [{"title": "第一条", "content": "第一条 完整正文。\n第一条之一 增补正文。"}]}})
        self.assertEqual(data["articles_by_int"], {"1": "完整正文。", "1之1": "增补正文。"})
        parsed = importer._parse_articles("　　第一条 正文引用第二条不切断。\n\n## 第二章 章节名称\n第二条 完整后文。")
        self.assertEqual(parsed["一"], "正文引用第二条不切断。")

    def handler(self, root, path):
        handler = serve.Handler.__new__(serve.Handler)
        handler.data_dir = root
        handler.path = path
        handler._json = lambda status, body: (status, body)
        return handler.do_GET()

    def write_statute(self, root, slug, text="普通文本", **metadata):
        value = {"id": slug, "slug": slug, "name": slug, "articles": {"一": text},
                 "articles_by_int": {"1": text}, "article_count": 1, "source": {}, **metadata}
        value["content_hash"] = content_hash(value)
        directory = root / "statutes"
        directory.mkdir(exist_ok=True)
        (directory / f"{slug}.json").write_text(json.dumps(value))

    def test_handler_rejects_traversal_and_bad_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_statute(root, "test")
            (root / "private.json").write_text('{"marker":"secret-test-only"}')
            (root / "statutes/link.json").symlink_to(root / "private.json")
            for path in ("/v1/laws/../private", "/v1/laws/%2e%2e%2fprivate", "/v1/laws/link"):
                status, body = self.handler(root, path)
                self.assertEqual(status, 404)
                self.assertNotIn("marker", body)
            for query in ("limit=x", "limit=-1", "limit=1001", "offset=-1"):
                self.assertEqual(self.handler(root, "/v1/laws?" + query)[0], 400)
            self.write_statute(root, "quarantined", retrieval_status="quarantined")
            self.assertEqual(self.handler(root, "/v1/statute/quarantined")[0], 404)

    def test_search_reaches_beyond_200_and_http_resolves_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(202):
                self.write_statute(root, f"law-{index:03}", "目标内容" if index == 201 else "普通内容",
                                   effective_date="2020-01-01", status="现行有效")
            rebuild_indexes(root)
            status, result = self.handler(root, "/v1/search?q=目标")
            self.assertEqual(status, 200)
            self.assertEqual(result["results"][0]["slug"], "law-201")
            self.write_statute(root, "law-000-quarantine", "目标内容", retrieval_status="quarantined")
            rebuild_indexes(root)
            build_sqlite.build_sqlite(root, root / "prc-law.db", verbose=False)
            self.assertEqual(self.handler(root, "/v1/search?q=目标")[1]["results"][0]["slug"], "law-201")
            self.assertEqual(self.handler(root, "/v1/resolve?law=law-201&as_of=2021-01-01")[1]["slug"], "law-201")
            self.assertEqual(self.handler(root, "/v1/resolve?law=law-201&as_of=2019-01-01")[0], 404)

    def test_verify_detects_index_missing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_statute(root, "valid")
            rebuild_indexes(root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verify.verify(root), 0)
                (root / "index/laws.jsonl").write_text('{"id":"missing"}\n')
                self.assertNotEqual(verify.verify(root), 0)

    def test_import_uses_out_dir_and_preserves_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "custom"
            previous, argv = importer.import_laws_data, sys.argv
            importer.import_laws_data = lambda cache: iter([
                {"name": "测试规则", "effective_date": day, "content": "第一条 " + text,
                 "_source": "isolated-fixture", "status": "现行有效"}
                for day, text in [("2020-01-01", "旧版本完整正文"), ("2024-01-01", "新版本完整正文")]])
            sys.argv = ["import.py", "--source", "laws-data", "--out-dir", str(output)]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(importer.main(), 0)
                    self.assertEqual(verify.verify(output), 0)
            finally:
                importer.import_laws_data, sys.argv = previous, argv
            self.assertEqual(len(list((output / "statutes").glob("*.json"))), 2)
            status, resolved = self.handler(output, "/v1/resolve?law=测试规则&as_of=2021-01-01")
            self.assertEqual(status, 200)
            self.assertEqual(resolved["effective_date"], "2020-01-01")

    def test_publication_rolls_back_if_postcheck_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, staged, backup = root / "old", root / "staged", root / "backup"
            old.mkdir()
            staged.mkdir()
            self.write_statute(old, "old")
            self.write_statute(staged, "new")
            rebuild_indexes(old)
            rebuild_indexes(staged)
            (staged / "recovery-report.json").write_text("{}")
            def validator(path):
                return 0 if path == staged.resolve() else 1
            with self.assertRaises(ValueError):
                promote(staged, old, backup, validator=validator)
            self.assertTrue((old / "statutes/old.json").exists())
            self.assertFalse((old / "statutes/new.json").exists())
            self.assertTrue((staged / "statutes/new.json").exists())


if __name__ == "__main__":
    unittest.main()
