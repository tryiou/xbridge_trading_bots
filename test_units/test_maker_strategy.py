import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.order_id_pool import OrderIdPool
from strategies.basicseller_strategy import BasicSellerStrategy


@pytest.fixture
def tmp_pool(tmp_path):
    return OrderIdPool(file_path=str(tmp_path / "pool.yaml"))


@pytest.fixture
def strategy(tmp_pool):
    """BasicSeller instance wired to mocked infra + its own temp notebook."""
    cm = MagicMock()
    cm.general_log = MagicMock()
    xbm = MagicMock()
    xbm.order_pool = tmp_pool
    xbm.getmyorders = AsyncMock(return_value=[])
    xbm.cancelorder = AsyncMock(return_value={"id": "x", "status": "canceled"})
    cm.xbridge_manager = xbm

    controller = MagicMock()
    controller.pairs_dict = {}
    controller.shutdown_event = asyncio.Event()

    strat = BasicSellerStrategy(cm, controller)
    strat._checkup_running = False
    strat._order_status_processor = None
    strat.tmp_pool = tmp_pool
    strat.xbm = xbm
    cm.strategy_instance = strat  # production wiring: cm owns this strategy
    return strat


def _tracked_pair(oid="tracked-1"):
    pair = MagicMock()
    pair.dex.order = {"id": oid, "status": "open"}
    return pair


def _seed_aged(pool, oid, age_s):
    """Insert a pooled id with a backdated posting time."""
    pool.add(oid, "T1/T2")
    with pool._lock:
        pool._ids[oid]["ts"] = time.time() - age_s


def _listing(oid, status, **extra):
    return {"id": oid, "status": status, "maker": "BLOCK", "taker": "LTC", **extra}


# ---------------------------------------------------------------------------
# Contract locks
# ---------------------------------------------------------------------------


def test_startup_tasks_are_original_unscoped_methods(strategy):
    """Regression lock: startup keeps wallet-wide cancel-all contract."""
    tasks = strategy.get_startup_tasks()
    assert tasks[0] == strategy.config_manager.xbridge_manager.cancelallorders
    assert tasks[1] == strategy.config_manager.xbridge_manager.dxflushcancelledorders


@pytest.mark.asyncio
async def test_exactly_one_listing_call_per_round(strategy):
    strategy.xbm.getmyorders = AsyncMock(return_value=[])
    await strategy.run_periodic_checkup()
    strategy.xbm.getmyorders.assert_awaited_once()


@pytest.mark.asyncio
async def test_never_probes_individual_ids(strategy):
    """The listing is the only source: no per-id dxGetOrder anywhere."""
    probe = AsyncMock(side_effect=AssertionError("per-id probing is forbidden"))
    strategy.xbm.getorderstatus = probe
    _seed_aged(strategy.tmp_pool, "old-1", 86400)
    strategy.xbm.getmyorders = AsyncMock(return_value=[])

    await strategy.run_periodic_checkup()

    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_flight_reentry_blocked(strategy):
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_getmyorders():
        started.set()
        await release.wait()
        return []

    strategy.xbm.getmyorders = AsyncMock(side_effect=slow_getmyorders)
    task = asyncio.create_task(strategy.run_periodic_checkup())
    await started.wait()
    second = asyncio.create_task(strategy.run_periodic_checkup())
    await asyncio.sleep(0.01)  # let the second call hit the guard
    assert second.done()  # returned immediately without running
    release.set()
    await task


@pytest.mark.asyncio
async def test_getmyorders_failure_quietly_aborts(strategy):
    strategy.tmp_pool.add("pooled-1", "A/B")
    strategy.xbm.getmyorders = AsyncMock(side_effect=RuntimeError("no rpc"))

    await strategy.run_periodic_checkup()  # must not raise

    assert strategy.tmp_pool.ids == {"pooled-1"}  # nothing mutated


@pytest.mark.asyncio
async def test_malformed_listing_aborts_quietly(strategy):
    strategy.xbm.getmyorders = AsyncMock(return_value="garbage")
    await strategy.run_periodic_checkup()  # must not raise
    strategy.xbm.cancelorder.assert_not_awaited()


# ---------------------------------------------------------------------------
# Ghost detection: pooled id seen alive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ghost_open_cancelled_and_kept_for_repeat_offenders(strategy):
    _seed_aged(strategy.tmp_pool, "ghost-1", 7200)
    strategy.xbm.getmyorders = AsyncMock(return_value=[_listing("ghost-1", "open")])

    await strategy.run_periodic_checkup()

    strategy.xbm.cancelorder.assert_awaited_once_with("ghost-1")
    strategy.config_manager.general_log.error.assert_called()  # loud
    assert "ghost-1" in strategy.tmp_pool.ids  # re-armed: next ghost also caught


@pytest.mark.asyncio
async def test_ghost_new_status_also_detected(strategy):
    _seed_aged(strategy.tmp_pool, "ghost-2", 7200)
    strategy.xbm.getmyorders = AsyncMock(return_value=[_listing("ghost-2", "new")])

    await strategy.run_periodic_checkup()

    strategy.xbm.cancelorder.assert_awaited_once_with("ghost-2")


@pytest.mark.asyncio
async def test_ghost_cancel_failure_stays_pooled_and_warns(strategy):
    _seed_aged(strategy.tmp_pool, "ghost-3", 7200)
    strategy.xbm.getmyorders = AsyncMock(return_value=[_listing("ghost-3", "open")])
    strategy.xbm.cancelorder = AsyncMock(side_effect=RuntimeError("rpc down"))

    await strategy.run_periodic_checkup()  # must not raise

    assert "ghost-3" in strategy.tmp_pool.ids
    strategy.config_manager.general_log.warning.assert_called()


@pytest.mark.asyncio
async def test_ghost_cancel_none_result_stays_pooled_and_warns(strategy):
    """Circuit-breaker-open / handler-abort surfaces as None: not a success."""
    _seed_aged(strategy.tmp_pool, "ghost-4", 7200)
    strategy.xbm.getmyorders = AsyncMock(return_value=[_listing("ghost-4", "open")])
    strategy.xbm.cancelorder = AsyncMock(return_value=None)

    await strategy.run_periodic_checkup()

    assert "ghost-4" in strategy.tmp_pool.ids
    strategy.config_manager.general_log.warning.assert_called()


# ---------------------------------------------------------------------------
# Legit resting states + invisibility of foreign orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canceled_expired_sightings_keep_vigilance_silently(strategy):
    """THE regression lock for the core correction: resting states never purge.

    canceled/expired sightings must leave the id pooled so a later daemon
    restart can still resurrect it into a detectable ghost.
    """
    _seed_aged(strategy.tmp_pool, "rest-1", 7200)
    _seed_aged(strategy.tmp_pool, "rest-2", 7200)
    strategy.xbm.getmyorders = AsyncMock(
        return_value=[
            _listing("rest-1", "canceled"),
            _listing("rest-2", "expired"),
        ]
    )

    await strategy.run_periodic_checkup()

    strategy.xbm.cancelorder.assert_not_awaited()
    assert strategy.tmp_pool.ids == {"rest-1", "rest-2"}
    strategy.config_manager.general_log.error.assert_not_called()
    strategy.config_manager.general_log.critical.assert_not_called()


@pytest.mark.asyncio
async def test_resurrection_after_canceled_rest_is_caught(strategy):
    """The full user scenario: cancel -> rest -> daemon restart -> ghost."""
    _seed_aged(strategy.tmp_pool, "phoenix", 7200)
    # Cycle 1: rests as canceled.
    strategy.xbm.getmyorders = AsyncMock(return_value=[_listing("phoenix", "canceled")])
    await strategy.run_periodic_checkup()
    assert "phoenix" in strategy.tmp_pool.ids

    # Daemon restart happens here; phoenix comes back OPEN.
    strategy.xbm.getmyorders = AsyncMock(return_value=[_listing("phoenix", "open")])
    await strategy.run_periodic_checkup()

    strategy.xbm.cancelorder.assert_awaited_once_with("phoenix")
    strategy.config_manager.general_log.error.assert_called()


@pytest.mark.asyncio
async def test_unknown_live_orders_are_structurally_invisible(strategy):
    """Manual operator orders / sibling instances: never even looked at."""
    strategy.xbm.getmyorders = AsyncMock(
        return_value=[
            _listing("manual-1", "open"),
            _listing("sibling-1", "new"),
            _listing("manual-2", "finished"),
            _listing("manual-3", "rolled back"),
        ]
    )

    await strategy.run_periodic_checkup()

    strategy.xbm.cancelorder.assert_not_awaited()
    strategy.config_manager.general_log.error.assert_not_called()
    strategy.config_manager.general_log.warning.assert_not_called()
    strategy.config_manager.general_log.critical.assert_not_called()


@pytest.mark.asyncio
async def test_own_tracked_order_never_touched(strategy):
    strategy.controller.pairs_dict = {"p": _tracked_pair("active-1")}
    strategy.tmp_pool.add("active-1", "A/B")
    strategy.xbm.getmyorders = AsyncMock(return_value=[_listing("active-1", "open")])

    await strategy.run_periodic_checkup()

    strategy.xbm.cancelorder.assert_not_awaited()


@pytest.mark.asyncio
async def test_sibling_session_ids_absent_from_notebook(strategy):
    """Per-session notebooks: sibling ids are simply not ours to see."""
    # Sibling session has its own pool file; our notebook stays empty.
    strategy.xbm.getmyorders = AsyncMock(
        return_value=[_listing("sibling-live", "open")]
    )

    await strategy.run_periodic_checkup()

    strategy.xbm.cancelorder.assert_not_awaited()
    assert strategy.tmp_pool.size == 0


# ---------------------------------------------------------------------------
# Final states observed: log once, prune
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blind_fill_logs_critical_once_and_prunes(strategy):
    _seed_aged(strategy.tmp_pool, "blind-1", 7200)
    listing = [_listing("blind-1", "finished")]
    strategy.xbm.getmyorders = AsyncMock(return_value=listing)

    await strategy.run_periodic_checkup()
    strategy.config_manager.general_log.critical.assert_called_once()
    assert strategy.tmp_pool.size == 0

    # Second round: entry pruned -> no repeat alarm.
    await strategy.run_periodic_checkup()
    assert strategy.config_manager.general_log.critical.call_count == 1


@pytest.mark.asyncio
async def test_failed_swap_states_warn_once_and_prune(strategy):
    for i, _status in enumerate(
        ("offline", "invalid", "rolled back", "rollback failed")
    ):
        _seed_aged(strategy.tmp_pool, f"broken-{i}", 7200)
    strategy.xbm.getmyorders = AsyncMock(
        return_value=[
            _listing("broken-0", "offline"),
            _listing("broken-1", "invalid"),
            _listing("broken-2", "rolled back"),
            _listing("broken-3", "rollback failed"),
        ]
    )

    await strategy.run_periodic_checkup()

    assert strategy.tmp_pool.size == 0
    assert strategy.config_manager.general_log.warning.call_count >= 4


@pytest.mark.asyncio
async def test_hot_path_finish_purges_before_checkup_sees_it(strategy):
    """Normal fills never appear as blind fills: hot path purges first."""
    strategy.tmp_pool.add("normal-fill", "A/B")
    # Simulate the hot path (processor._handle_finished_order already purged).
    strategy.tmp_pool.purge("normal-fill")
    strategy.xbm.getmyorders = AsyncMock(
        return_value=[_listing("normal-fill", "finished")]
    )

    await strategy.run_periodic_checkup()

    strategy.config_manager.general_log.critical.assert_not_called()


@pytest.mark.asyncio
async def test_state_change_logging_no_spam(strategy):
    """A stuck exotic stage logs once per change, not every round."""
    _seed_aged(strategy.tmp_pool, "weird-1", 7200)
    strategy.xbm.getmyorders = AsyncMock(
        return_value=[_listing("weird-1", "accepting")]
    )
    await strategy.run_periodic_checkup()
    first = strategy.config_manager.general_log.info.call_count

    await strategy.run_periodic_checkup()
    second = strategy.config_manager.general_log.info.call_count

    assert first >= 1  # flagged on appearance
    assert second == first  # silent on unchanged repeat


# ---------------------------------------------------------------------------
# Hygiene mechanics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_enforced_each_round(strategy):
    spy = MagicMock()
    strategy.tmp_pool.enforce_ttl = spy
    await strategy.run_periodic_checkup()
    spy.assert_called_once()


@pytest.mark.asyncio
async def test_absent_pooled_id_survives_session_scoping(strategy):
    """Absence proves nothing after a daemon restart: keep until TTL/budget."""
    _seed_aged(strategy.tmp_pool, "dormant-1", 7200)
    strategy.xbm.getmyorders = AsyncMock(return_value=[])

    await strategy.run_periodic_checkup()

    assert "dormant-1" in strategy.tmp_pool.ids


@pytest.mark.asyncio
async def test_flush_persisted_after_round(strategy, tmp_path):
    _seed_aged(strategy.tmp_pool, "persist-me", 60)
    strategy.xbm.getmyorders = AsyncMock(return_value=[])
    await strategy.run_periodic_checkup()

    from definitions.order_id_pool import OrderIdPool

    reloaded = OrderIdPool(file_path=str(tmp_path / "pool.yaml"))
    assert "persist-me" in reloaded.ids
