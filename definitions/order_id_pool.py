"""Per-session durable notebook of posted order ids.

Every successfully posted XBridge order id is recorded here and kept after
completion. The pool exists because ``dxGetMyOrders`` only reports orders of
the *current* daemon session: an order that resurrects after a core wallet
restart is invisible unless we remember its id.

Semantics (v9):
- ``canceled``/``expired`` sightings are LEGIT resting states: the id stays
  pooled and vigilant, because a later daemon restart can resurrect it.
- ``open``/``new`` sighting of a pooled id that nobody tracks = GHOST:
  log + cancel; the id STAYS pooled so repeat offenders are caught.
- Observed FINAL states (``finished``, failed-swap family) are logged once,
  then pruned: the mystery is resolved.
- Absent from listing = no order exists right now = dormant, kept.

Exits are therefore only: self-observed finish (hot path), TTL, byte budget.
Writes are coalesced behind a dirty flag and persisted atomically (see
``yaml_utils.save_yaml``).
"""

import logging
import os
import threading
import time
from typing import Any

from definitions.constants import (
    POOL_FILE_BUDGET_BYTES,
    POOL_TTL_DAYS,
)
from definitions.yaml_utils import dumps_yaml, load_yaml, save_yaml

logger = logging.getLogger(__name__)

POOL_TTL_SECONDS: float = POOL_TTL_DAYS * 86400.0

_META_KEYS = ("sym", "ts", "last_status")


class OrderIdPool:
    """Thread-safe bounded ``{order_id: meta}`` store backed by one YAML file."""

    def __init__(
        self,
        file_path: str,
        budget_bytes: int = POOL_FILE_BUDGET_BYTES,
        ttl_seconds: float = POOL_TTL_SECONDS,
    ) -> None:
        self._path = file_path
        self._budget_bytes = budget_bytes
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._ids: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.load()

    # ------------------------------------------------------------------ state

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._ids)

    @property
    def ids(self) -> set[str]:
        with self._lock:
            return set(self._ids)

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._dirty

    def last_status(self, order_id: str) -> str | None:
        """Previously seen listing status for this id, if any."""
        with self._lock:
            meta = self._ids.get(order_id)
            value = meta.get("last_status") if meta else None
            return str(value) if value else None

    def set_last_status(self, order_id: str, status: str) -> None:
        with self._lock:
            meta = self._ids.get(order_id)
            if meta is not None and meta.get("last_status") != status:
                meta["last_status"] = status
                self._dirty = True

    # ------------------------------------------------------------- mutations

    def add(self, order_id: str, symbol: str) -> None:
        """Record a posted order id. Duplicate ids keep their original entry."""
        if not order_id:
            return
        with self._lock:
            if order_id in self._ids:
                return
            self._ids[order_id] = {"sym": symbol, "ts": time.time()}
            self._dirty = True

    def purge(self, order_id: str) -> None:
        """Remove an id whose fate was observed as final."""
        with self._lock:
            if self._ids.pop(order_id, None) is not None:
                self._dirty = True

    def enforce_ttl(self, now: float | None = None) -> int:
        """Purge entries older than the TTL. Returns the number purged."""
        now = time.time() if now is None else now
        with self._lock:
            stale = [
                oid
                for oid, meta in self._ids.items()
                if now - float(meta.get("ts", 0.0)) > self._ttl_seconds
            ]
            for oid in stale:
                self.purge(oid)
            if stale:
                logger.info("Order-id pool TTL-purged %d stale entries", len(stale))
            return len(stale)

    # ------------------------------------------------------------------- disk

    def flush(self, force: bool = False) -> bool:
        """Persist to disk when dirty (or forced). Returns True if written."""
        with self._lock:
            if not self._dirty and not force:
                return False
            try:
                payload = self._payload_under_budget()
                save_yaml(self._path, payload)
                self._dirty = False
                return True
            except Exception:
                logger.exception("Failed to persist order-id pool to %s", self._path)
                return False

    # -------------------------------------------------------------- internals

    def load(self) -> None:
        try:
            data = load_yaml(self._path)
        except FileNotFoundError:
            return
        except Exception:
            self._quarantine_corrupt_file()
            return
        if not isinstance(data, dict):
            self._quarantine_corrupt_file()
            return
        valid: dict[str, dict[str, Any]] = {}
        for oid, meta in data.items():
            if isinstance(oid, str) and oid and isinstance(meta, dict):
                try:
                    entry: dict[str, Any] = {
                        "sym": str(meta.get("sym", "?")),
                        "ts": float(meta.get("ts", 0.0)),
                    }
                    if meta.get("last_status"):
                        entry["last_status"] = str(meta["last_status"])
                    valid[oid] = entry
                except (TypeError, ValueError):
                    continue
        self._ids = valid
        self._dirty = False

    def _quarantine_corrupt_file(self) -> None:
        logger.warning("Order-id pool at %s unreadable; starting fresh", self._path)
        self._ids = {}
        self._dirty = False
        try:
            os.replace(self._path, f"{self._path}.corrupt-{int(time.time())}")
        except OSError as e:
            logger.warning("Could not quarantine corrupt pool file: %s", e)

    def _payload_under_budget(self) -> dict[str, dict[str, Any]]:
        """Drop oldest entries until the serialized payload fits the budget."""
        payload = dict(self._ids)
        while payload:
            size = len(dumps_yaml(payload))
            if size <= self._budget_bytes:
                break
            per_entry = max(size // len(payload), 1)
            drop_n = max((size - self._budget_bytes) // per_entry + 1, 1)
            oldest = sorted(payload.items(), key=lambda kv: float(kv[1].get("ts", 0.0)))
            for oid, _ in oldest[:drop_n]:
                del payload[oid]
            logger.info(
                "Order-id pool exceeded %d-byte budget; oldest entries evicted",
                self._budget_bytes,
            )

        dropped = set(self._ids) - set(payload)
        if dropped:
            self._ids = payload
        return payload
