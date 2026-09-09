"""Bounded per-principal rate limits; principals come only from server credentials."""
import hashlib
import hmac
import threading
import time


class AccessPolicy:
    def __init__(self, users: dict, per_minute: int = 120, *, clock=time.monotonic):
        if not users or len(users) > 1000 or per_minute < 1:
            raise ValueError("invalid users or rate limit")
        self.users, self.limit, self.clock = users, per_minute, clock
        self.lock, self.windows = threading.Lock(), {}
        seen = set()
        for owner, data in users.items():
            if not isinstance(owner, str) or not owner or set(data) != {"token", "role"} or data["role"] not in ("user", "reviewer"):
                raise ValueError("invalid identity configuration")
            token = data["token"]
            if not isinstance(token, str) or len(token) < 32 or token in seen:
                raise ValueError("tokens must be unique and at least 32 characters")
            seen.add(token)

    def authenticate(self, authorization: str) -> dict | None:
        for owner, data in self.users.items():
            if hmac.compare_digest(authorization.encode(), ("Bearer " + data["token"]).encode()):
                return {"owner": owner, "role": data["role"]}
        return None

    def allowed(self, owner: str) -> bool:
        with self.lock:
            current = int(self.clock() // 60)
            window, count = self.windows.get(owner, (current, 0))
            count = 0 if window != current else count
            if count >= self.limit:
                return False
            self.windows[owner] = current, count + 1
            return True
