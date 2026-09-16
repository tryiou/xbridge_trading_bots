import os
import sys
import threading
import time

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.order_id_pool import OrderIdPool


@pytest.fixture
def pool_path(tmp_path):
    return str(tmp_path / "pool.yaml")


def _seed(pool, ids_with_ts):
    """Insert entries with deterministic timestamps (older first)."""
    for oid, ts in ids_with_ts:
        pool._ids[oid] = {"sym": "T1/T2", "ts": ts}
    pool._dirty = True


def test_add_and_dedup(pool_path):
    pool = OrderIdPool(file_path=pool_path)
    pool.add("a" * 64, "BLOCK/LTC")
    pool.add("a" * 64, "BLOCK/LTC")  # duplicate ignored
    pool.add("", "X/Y")  # empty id guarded
    assert pool.size == 1
    assert pool.ids == {"a" * 64}


def test_purge_removes_entry(pool_path):
    pool = OrderIdPool(file_path=pool_path)
    _seed(pool, [("a", 1.0), ("b", 2.0)])
    pool.purge("a")
    assert pool.ids == {"b"}
    pool.purge("missing")  # no-op, no raise


def test_enforce_ttl_boundary(pool_path):
    pool = OrderIdPool(file_path=pool_path, ttl_seconds=100.0)
    _seed(pool, [("fresh", time.time() - 99.0), ("stale", time.time() - 101.0)])
    purged = pool.enforce_ttl()
    assert purged == 1
    assert pool.ids == {"fresh"}


def test_budget_eviction_drops_oldest(pool_path):
    long_id = "x" * 64
    pool = OrderIdPool(file_path=pool_path, budget_bytes=300)
    for i in range(20):
        pool.add(f"{long_id}{i:04d}", "BLOCK/LTC")
    entries = [(oid, float(i)) for i, (oid, _) in enumerate(sorted(pool._ids.items()))]
    _seed(pool, entries)  # deterministic ages: earlier key = older

    written = pool.flush()
    assert written is True

    with open(pool_path) as f:
        assert len(f.read()) <= 300 * 4  # serialized file respects budget scale
    survivors = pool.ids
    assert 0 < len(survivors) < 20
    # Newest entries must survive; oldest must go.
    assert f"{long_id}0019" in survivors
    assert f"{long_id}0000" not in survivors


def test_flush_dirty_gating(pool_path):
    pool = OrderIdPool(file_path=pool_path)
    assert pool.flush() is False  # clean -> no write
    assert not os.path.exists(pool_path)
    pool.add("a", "S/T")
    assert pool.dirty is True
    assert pool.flush() is True
    assert os.path.exists(pool_path)
    assert pool.flush() is False  # no longer dirty


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "pool.yaml")
    p1 = OrderIdPool(file_path=path)
    p1.add("order-1", "BLOCK/LTC")
    p1.add("order-2", "PIVX/BTC")
    p1.flush()

    p2 = OrderIdPool(file_path=path)
    assert p2.ids == {"order-1", "order-2"}
    assert p2.dirty is False  # loaded state is considered persisted


def test_corrupt_file_quarantined(tmp_path):
    path = tmp_path / "pool.yaml"
    path.write_text("- just\n- a\n- list\nnot: [valid")  # unparseable
    pool = OrderIdPool(file_path=str(path))
    assert pool.size == 0
    quarantined = list(tmp_path.glob("pool.yaml.corrupt-*"))
    assert len(quarantined) == 1


def test_non_dict_payload_quarantined(tmp_path):
    path = tmp_path / "pool.yaml"
    path.write_text("- a\n- b\n")  # valid YAML, wrong shape
    pool = OrderIdPool(file_path=str(path))
    assert pool.size == 0
    assert len(list(tmp_path.glob("pool.yaml.corrupt-*"))) == 1


def test_malformed_entries_skipped_on_load(tmp_path):
    path = tmp_path / "pool.yaml"
    path.write_text(
        "good-id:\n  sym: BLOCK/LTC\n  ts: 100.0\n"
        "bad-meta: just_a_string\n"
        "bad-ts:\n  sym: X/Y\n  ts: not_a_number\n"
    )
    pool = OrderIdPool(file_path=str(path))
    assert pool.ids == {"good-id"}
    assert pool._ids["good-id"]["ts"] == pytest.approx(100.0)


def test_thread_safety_smoke(pool_path):
    pool = OrderIdPool(file_path=pool_path)
    n_threads, per_thread = 8, 50

    def worker(offset):
        for i in range(per_thread):
            pool.add(f"id-{offset}-{i}", "A/B")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert pool.size == n_threads * per_thread


def test_add_does_not_write_immediately_but_marks_dirty(pool_path):
    from unittest.mock import patch

    pool = OrderIdPool(file_path=pool_path)
    with patch("definitions.order_id_pool.save_yaml") as mock_save:
        pool.add("a", "A/B")
        mock_save.assert_not_called()  # writes are explicit flush() decisions
        assert pool.dirty is True
