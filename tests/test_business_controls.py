import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common_bootstrap
from cryptography.fernet import Fernet
from law_common.rules import cn_to_int, choose_version
from law_common.config import settings
from law_common.matters import MatterStore
from law_common.costs import CostLedger
from law_common.access import AccessPolicy
from law_common.official import check, approve, status, validate_url


class BusinessTests(unittest.TestCase):
    def test_shared_number_parser_rejects_collision(self):
        self.assertEqual(cn_to_int("一千二百六十"), 1260)
        self.assertEqual(cn_to_int("一千零五"), 1005)
        self.assertIsNone(cn_to_int("八二十"))
        self.assertIsNone(cn_to_int("三一十"))

    def test_config_rejects_secret_or_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"; path.write_text('{"PKULAW_TOKEN":"not-real"}')
            prior = os.environ.get("PRC_LAW_CONFIG"); os.environ["PRC_LAW_CONFIG"] = str(path)
            try:
                with self.assertRaises(ValueError): settings()
                path.write_text('{"retention_days":0}')
                with self.assertRaises(ValueError): settings()
            finally:
                if prior is None: os.environ.pop("PRC_LAW_CONFIG", None)
                else: os.environ["PRC_LAW_CONFIG"] = prior

    def test_encryption_isolation_deletion_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [1000.0]; path = Path(tmp) / "store.db"
            store = MatterStore(path, Fernet.generate_key(), retention_days=1, clock=lambda: clock[0])
            with self.assertRaises(ValueError): store.create("alice", {"summary":"private-case-marker"}, False)
            created = store.create("alice", {"summary":"private-case-marker"}, True)
            self.assertNotIn(b"private-case-marker", path.read_bytes())
            with self.assertRaises(LookupError): store.export("bob", created["id"])
            with self.assertRaises(LookupError): store.delete("bob", created["id"])
            ticket = store.ticket("alice", created["id"], "human_review", "核对期限")
            with self.assertRaises(LookupError): store.reviewer_view("reviewer", ticket["ticket_id"])
            store.assign("reviewer", ticket["ticket_id"])
            self.assertEqual(store.reviewer_view("reviewer", ticket["ticket_id"])["payload"]["summary"], "private-case-marker")
            with self.assertRaises(LookupError): store.resolve("other", ticket["ticket_id"], "不能处理")
            store.resolve("reviewer", ticket["ticket_id"], "已核对，请补充日期")
            self.assertEqual(store.export("alice", created["id"])["tickets"][0]["status"], "resolved")
            store.delete("alice", created["id"])
            self.assertEqual(store.queue(), [])
            expired = store.create("alice", {"summary":"will expire"}, True)
            clock[0] += 86401
            with self.assertRaises(LookupError): store.export("alice", expired["id"])
            self.assertEqual(store.purge_expired(), 1)
            held = store.create("alice", {"summary":"retained for review"}, True)
            store.set_hold(held["id"], True, "synthetic legal hold")
            clock[0] += 86401
            self.assertEqual(store.purge_expired(), 0)
            with self.assertRaises(ValueError): store.delete("alice", held["id"])
            store.set_hold(held["id"], False, "synthetic hold released")
            self.assertEqual(store.purge_expired(), 1)

    def test_owner_comes_from_token_and_rate_limit_is_bounded(self):
        policy = AccessPolicy({"alice":{"token":"a"*32,"role":"user"}}, 2, clock=lambda: 1)
        self.assertIsNone(policy.authenticate("Bearer wrong"))
        self.assertEqual(policy.authenticate("Bearer " + "a"*32)["owner"], "alice")
        self.assertTrue(policy.allowed("alice")); self.assertTrue(policy.allowed("alice")); self.assertFalse(policy.allowed("alice"))

    def test_official_changed_failed_stale_and_hash_bound_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); now = datetime.now(timezone.utc)
            entry = {"law":"测试法", "url":"https://www.gov.cn/test"}
            result = check(root, entry, fetcher=lambda _: b"official-fixture-v1", now=now)
            self.assertEqual(result["status"], "pending_review")
            with self.assertRaises(ValueError): approve(root, result["id"], "0"*64, "reviewer", "测试范围")
            approve(root, result["id"], result["observed_sha256"], "reviewer", "测试范围", now=now)
            self.assertEqual(status(root,"测试法",7,now=now)["status"], "listed_pages_reviewed")
            self.assertEqual(status(root,"测试法",7,now=now+timedelta(days=8))["records"][0]["status"], "stale")
            def failure(_): raise OSError("network unavailable")
            failed = check(root, entry, fetcher=failure, now=now+timedelta(days=1))
            self.assertEqual(failed["checked_at"], now.isoformat())
            self.assertEqual(failed["status"], "check_failed")
            changed = check(root, entry, fetcher=lambda _: b"official-fixture-v2", now=now)
            self.assertEqual(changed["status"], "pending_review")
            self.assertFalse(status(root,"测试法",7,now=now)["official_current_validity_proven"])
            for url in ("http://www.gov.cn/a", "https://127.0.0.1/a", "https://www.gov.cn.evil.test/a", "https://user@www.gov.cn/a"):
                with self.assertRaises(ValueError): validate_url(url)

    def test_unknown_cost_not_zero_and_estimate_not_invoice(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CostLedger(Path(tmp)/"cost.db")
            self.assertIsNone(ledger.report()["total_api_cost_cny"])
            identifier = ledger.start("s1", "provider", "operation")
            ledger.finish(identifier, "failed", 20)
            self.assertIsNone(ledger.report()["total_api_cost_cny"])
            ledger.start("s1", "provider", "operation", {"cny_per_call":"0.25","evidence":"synthetic-fixture-not-real-price"})
            report = ledger.report()
            self.assertEqual(report["priced_subtotal_cny"], "0.25")
            self.assertEqual(report["services"], 1)
            self.assertIsNone(report["full_service_cost_cny"])
