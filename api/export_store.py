"""Expiring, encrypted export state shared by API and workers through Redis."""

import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
from functools import lru_cache

from cryptography.fernet import Fernet

from .settings import get_settings


class ExportStore:
    def __init__(self, redis_url=None, secret="local-test-only", local_path=None):
        self._redis = None
        self._local_path = local_path
        if redis_url:
            from redis import Redis
            self._redis = Redis.from_url(redis_url)
        key = hashlib.sha256(("channelmind-exports-v1:" + secret).encode()).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(key))
        self._memory = {}
        self._lock = threading.RLock()
        if local_path and not redis_url:
            os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
            with self._connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, expires REAL NOT NULL, value BLOB NOT NULL)")

    def _connect(self):
        db = sqlite3.connect(self._local_path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _key(self, key):
        return "channelmind:exports:" + key

    def put(self, key, value, expires_at, *, only_if_absent=False):
        ttl = int(expires_at - time.time())
        if ttl <= 0:
            raise ValueError("Export session expired")
        encrypted = self._cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())
        if self._redis is not None:
            return bool(self._redis.set(self._key(key), encrypted, ex=ttl, nx=only_if_absent))
        if self._local_path and not key.startswith("worker:"):
            with self._lock, self._connect() as db:
                db.execute("DELETE FROM entries WHERE expires <= ?", (time.time(),))
                if only_if_absent:
                    cursor = db.execute("INSERT OR IGNORE INTO entries VALUES (?, ?, ?)", (key, expires_at, encrypted))
                    return cursor.rowcount == 1
                db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?)", (key, expires_at, encrypted))
                return True
        with self._lock:
            # Local mode is deliberately ephemeral, but still prunes expired values.
            for expired in [k for k, (end, _) in self._memory.items() if end <= time.time()]:
                del self._memory[expired]
            if only_if_absent and key in self._memory:
                return False
            self._memory[key] = (expires_at, encrypted)
            return True

    def get(self, key, *, consume=False):
        if self._redis is not None:
            raw = self._redis.getdel(self._key(key)) if consume else self._redis.get(self._key(key))
        elif self._local_path and not key.startswith("worker:"):
            with self._lock, self._connect() as db:
                row = db.execute("SELECT expires, value FROM entries WHERE key = ?", (key,)).fetchone()
                if row and (row[0] <= time.time() or consume):
                    db.execute("DELETE FROM entries WHERE key = ?", (key,))
                raw = row[1] if row and row[0] > time.time() else None
        else:
            with self._lock:
                entry = self._memory.pop(key, None) if consume else self._memory.get(key)
                if not entry or entry[0] <= time.time():
                    self._memory.pop(key, None)
                    return None
                raw = entry[1]
        return json.loads(self._cipher.decrypt(raw)) if raw else None

    def delete(self, key):
        if self._redis is not None:
            self._redis.delete(self._key(key))
        elif self._local_path and not key.startswith("worker:"):
            with self._lock, self._connect() as db:
                db.execute("DELETE FROM entries WHERE key = ?", (key,))
        else:
            with self._lock:
                self._memory.pop(key, None)

    def release(self, key, value):
        """Release a lease only if it still belongs to this worker."""
        if self._redis is not None:
            raw = self._redis.get(self._key(key))
            if raw and json.loads(self._cipher.decrypt(raw)) == value:
                self._redis.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0",
                    1, self._key(key), raw,
                )
        elif self._local_path and not key.startswith("worker:"):
            with self._lock, self._connect() as db:
                row = db.execute("SELECT value FROM entries WHERE key = ? AND expires > ?", (key, time.time())).fetchone()
                if row and json.loads(self._cipher.decrypt(row[0])) == value:
                    db.execute("DELETE FROM entries WHERE key = ? AND value = ?", (key, row[0]))
        else:
            with self._lock:
                if self.get(key) == value:
                    self.delete(key)


@lru_cache(maxsize=1)
def get_export_store():
    settings = get_settings()
    return ExportStore(settings.redis_url, settings.app_secret,
                       local_path=None if settings.redis_url else settings.export_store_path)
