import contextlib
import http.client
import io
import tempfile
import threading
import unittest
from pathlib import Path

import test_data_safety
from data_store import rebuild_indexes
from build_sqlite import build_sqlite
from service_runtime import BoundedHTTPServer, readiness, validate_config
from serve import Handler


class ServiceTests(unittest.TestCase):
    def test_startup_security_and_readiness(self):
        with self.assertRaises(ValueError):
            validate_config("0.0.0.0", 8765, 16, "")
        with self.assertRaises(ValueError):
            validate_config("127.0.0.1", 8765, 0, "")
        validate_config("127.0.0.1", 8765, 16, "")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(readiness(root)["ready"])
            test_data_safety.DataSafetyTests().write_statute(root, "valid")
            rebuild_indexes(root)
            self.assertTrue(readiness(root)["ready"])
            self.assertFalse(readiness(root, True)["ready"])
            build_sqlite(root, root / "prc-law.db", False)
            self.assertTrue(readiness(root, True)["ready"])
            (root / "index/laws.jsonl").write_text("\n")
            self.assertFalse(readiness(root, True)["ready"])

    def test_real_http_auth_readiness_overload_and_log_privacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            entered, release = threading.Event(), threading.Event()
            class FixtureHandler(Handler):
                access_token = "test-only-token-" * 3
                data_dir = Path(tmp)
                require_db = True
                def _get(self):
                    if self.path == "/blocked":
                        entered.set()
                        release.wait(5)
                        return self._json(200, {"ok": True})
                    return super()._get()
            server = BoundedHTTPServer(("127.0.0.1", 0), FixtureHandler, workers=1)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            def get(path, token=""):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    connection.request("GET", path, headers={"Authorization": "Bearer " + token})
                    response = connection.getresponse()
                    return response.status, response.read(), dict(response.getheaders())
                finally:
                    connection.close()
            logs = io.StringIO()
            with contextlib.redirect_stderr(logs):
                thread.start()
                try:
                    self.assertEqual(get("/healthz")[0], 200)
                    self.assertEqual(get("/readyz")[0], 401)
                    status, _, headers = get("/readyz", FixtureHandler.access_token)
                    self.assertEqual(status, 503)
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertTrue(headers["X-Request-ID"])
                    self.assertEqual(get("/v1/search?q=private-client-matter")[0], 401)
                    busy = threading.Thread(target=get, args=("/blocked", FixtureHandler.access_token))
                    busy.start()
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(get("/healthz")[0], 503)
                    release.set()
                    busy.join(5)
                    self.assertEqual(server.metrics.snapshot()["overload_rejections"], 1)
                finally:
                    release.set()
                    server.shutdown()
                    thread.join(5)
                    server.server_close()
            self.assertNotIn("private-client-matter", logs.getvalue())
            self.assertNotIn(FixtureHandler.access_token, logs.getvalue())
            self.assertNotIn("127.0.0.1", logs.getvalue())
