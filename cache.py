"""
cache.py
========
SQLite-backed local cache for BIN metadata.

Why SQLite instead of plain JSON?
  - Concurrent-safe for async workloads (WAL mode)
  - Survives bot restarts without manual serialization
  - Fast keyed lookups even with millions of entries
  - Optional TTL support (unused here but easy to add)
"""

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Path to the cache database file (relative to project root)
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "bin_cache.db"


class BINCache:
    """
    Thread-safe SQLite cache for BIN metadata.

    The cache stores metadata dicts as JSON blobs keyed by the 6-digit BIN string.
    Hit/miss counters are kept in-memory for the /stats command.

    Thread safety:
        SQLite connections are *not* safe to share across threads, so we use
        thread-local storage (threading.local) to give each thread its own
        connection. In Python's asyncio model all coroutines run on one OS
        thread by default, so practically this means one connection total —
        but the design is safe even if you switch to a thread-pool executor.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path or DEFAULT_DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._local = threading.local()  # per-thread connection storage

        # In-memory stats counters
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()  # protect counter updates

        # Ensure schema is created on startup
        conn = self._conn()
        self._create_schema(conn)
        logger.info("BINCache initialised at %s", self._db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        """
        Return a thread-local SQLite connection.
        Creates and caches the connection on first access per thread.
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            # WAL mode: readers don't block writers; much better for async use
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            logger.debug("Opened SQLite connection (thread %s)", threading.current_thread().name)
        return self._local.conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create the bins table if it doesn't exist yet."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bins (
                bin         TEXT PRIMARY KEY,
                metadata    TEXT NOT NULL,          -- JSON blob
                created_at  DATETIME DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, bin_number: str) -> Optional[Dict[str, str]]:
        """
        Return cached metadata for *bin_number*, or None if not cached.
        Updates the in-memory hit/miss counter.
        """
        conn = self._conn()
        row = conn.execute(
            "SELECT metadata FROM bins WHERE bin = ?", (bin_number,)
        ).fetchone()

        with self._lock:
            if row:
                self._hits += 1
                return json.loads(row["metadata"])
            else:
                self._misses += 1
                return None

    def set(self, bin_number: str, metadata: Dict[str, str]) -> None:
        """
        Store *metadata* for *bin_number*.
        Uses INSERT OR REPLACE so re-fetching an updated result is handled.
        """
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO bins (bin, metadata) VALUES (?, ?)",
            (bin_number, json.dumps(metadata)),
        )
        conn.commit()

    def stats(self) -> Dict[str, int]:
        """Return a dict with cache statistics."""
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) FROM bins").fetchone()[0]
        with self._lock:
            return {
                "total_cached": total,
                "hits": self._hits,
                "misses": self._misses,
            }

    def close(self) -> None:
        """Close the thread-local connection (call on shutdown)."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
