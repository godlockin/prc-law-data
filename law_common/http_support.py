"""Readiness, bounded concurrency and privacy-preserving request metrics."""
from __future__ import annotations

import http.server
import ipaddress
import sqlite3
import threading
import json
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path


def validate_config(host: str, port: int, workers: int, token: str) -> None:
    if not 0 <= port <= 65535 or not 1 <= workers <= 128:
        raise ValueError("port must be 0..65535 and workers 1..128")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if (token and len(token) < 32) or (not loopback and not token):
        raise ValueError("non-loopback service requires PRC_LAW_DATA_TOKEN (at least 32 characters)")


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = Counter()
        self.elapsed_seconds = 0.0
        self.overload_rejections = 0

    def record(self, status: int, seconds: float) -> None:
        with self.lock:
            self.requests[str(status)] += 1
            self.elapsed_seconds += seconds

    def snapshot(self) -> dict:
        with self.lock:
            return {"requests_by_status": dict(self.requests), "request_seconds_total": self.elapsed_seconds,
                    "overload_rejections": self.overload_rejections}


class BoundedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, workers: int = 16):
        self.slots = threading.BoundedSemaphore(workers)
        self.metrics = Metrics()
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        request.settimeout(10)
        if not self.slots.acquire(blocking=False):
            with self.metrics.lock:
                self.metrics.overload_rejections += 1
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 1\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        error_type = sys.exc_info()[0]
        print(json.dumps({"event": "http_handler_failed", "error_type": error_type.__name__ if error_type else "unknown"}),
              file=sys.stderr, flush=True)
