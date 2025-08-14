import os
import sys
import json
import time
import shutil
from unittest.mock import MagicMock

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.trade_state import TradeState


# Mock strategy and config_manager for tests
@pytest.fixture
def mock_strategy():
    """Creates a mock strategy object with necessary attributes for TradeState."""
    strategy = MagicMock()
    strategy.config_manager = MagicMock()
    # Use a temporary directory for test state files
    strategy.config_manager.ROOT_DIR = '/tmp/test_trade_state_root'
    strategy.config_manager.general_log = MagicMock()
    strategy.test_mode = True
    return strategy


@pytest.fixture
def mock_strategy_prod_mode():
    """Creates a mock strategy object with test_mode=False."""
    strategy = MagicMock()
    strategy.config_manager = MagicMock()
    strategy.config_manager.ROOT_DIR = '/tmp/test_trade_state_root'
    strategy.config_manager.general_log = MagicMock()
    strategy.test_mode = False
    return strategy


@pytest.fixture
def trade_state_manager(mock_strategy):
    """
    A fixture that provides a TradeState instance and handles setup/teardown
    of the test directory.
    """
    # Setup: ensure the test directory is clean before each test
    state_dir = TradeState._get_state_dir(mock_strategy)
    if os.path.exists(state_dir):
        shutil.rmtree(state_dir)
    os.makedirs(state_dir)

    ts = TradeState(mock_strategy, 'test_check_id_123')

    yield ts

    # Teardown: clean up the directory after each test
    if os.path.exists(state_dir):
        shutil.rmtree(state_dir)


def test_trade_state_initialization(trade_state_manager, mock_strategy):
    """Tests that TradeState is initialized correctly."""
    assert trade_state_manager.check_id == 'test_check_id_123'
    assert trade_state_manager.strategy == mock_strategy
    state_dir = TradeState._get_state_dir(mock_strategy)
    assert trade_state_manager.state_dir == state_dir
    assert os.path.exists(state_dir)
    assert trade_state_manager.state_file_path == os.path.join(state_dir, 'test_check_id_123.json')
    assert trade_state_manager.state_data == {'check_id': 'test_check_id_123'}


def test_save_state(trade_state_manager):
    """Tests saving the trade state to a file."""
    status = 'test_status'
    data = {'key': 'value', 'num': 123}

    trade_state_manager.save(status, data)

    assert os.path.exists(trade_state_manager.state_file_path)
    with open(trade_state_manager.state_file_path, 'r') as f:
        saved_data = json.load(f)

    assert saved_data['check_id'] == 'test_check_id_123'
    assert saved_data['status'] == status
    assert saved_data['key'] == 'value'
    assert saved_data['num'] == 123
    assert 'timestamp' in saved_data
    assert isinstance(saved_data['timestamp'], float)


def test_delete_state(trade_state_manager):
    """Tests deleting a state file."""
    # First save a file to be deleted
    trade_state_manager.save('deleting', {})
    assert os.path.exists(trade_state_manager.state_file_path)

    trade_state_manager.delete()

    assert not os.path.exists(trade_state_manager.state_file_path)


def test_archive_state(trade_state_manager):
    """Tests archiving a state file."""
    trade_state_manager.save('archiving', {'some_data': 'foo'})
    assert os.path.exists(trade_state_manager.state_file_path)

    reason = 'test_reason'
    trade_state_manager.archive(reason)

    assert not os.path.exists(trade_state_manager.state_file_path)
    archive_dir = os.path.join(trade_state_manager.state_dir, 'archive')
    assert os.path.exists(archive_dir)

    archived_files = os.listdir(archive_dir)
    assert len(archived_files) == 1
    assert reason in archived_files[0]
    assert 'test_check_id_123' in archived_files[0]


def test_get_unfinished_trades(mock_strategy, trade_state_manager):
    """Tests retrieving all unfinished trade states."""
    # Create a couple of state files
    state1 = TradeState(mock_strategy, 'unfinished1')
    state1.save('pending', {'data': 1})

    state2 = TradeState(mock_strategy, 'unfinished2')
    state2.save('in_progress', {'data': 2})

    # Archive one to make sure it's not picked up
    state3 = TradeState(mock_strategy, 'archived1')
    state3.save('to_archive', {})
    state3.archive('done')

    unfinished = TradeState.get_unfinished_trades(mock_strategy)
    assert len(unfinished) == 2

    check_ids = {u['check_id'] for u in unfinished}
    assert 'unfinished1' in check_ids
    assert 'unfinished2' in check_ids


def test_cleanup_all_states(mock_strategy):
    """Tests the cleanup of all state files and directories."""
    state_dir = TradeState._get_state_dir(mock_strategy)
    archive_dir = os.path.join(state_dir, 'archive')

    # Create some files and directories
    os.makedirs(archive_dir, exist_ok=True)
    with open(os.path.join(state_dir, 'active.json'), 'w') as f:
        f.write('{}')
    with open(os.path.join(archive_dir, 'archived.json'), 'w') as f:
        f.write('{}')

    assert os.path.exists(state_dir)
    assert os.path.exists(archive_dir)

    TradeState.cleanup_all_states(mock_strategy)

    # The directory itself should exist but be empty
    assert os.path.exists(state_dir)
    assert not os.listdir(state_dir)


def test_get_state_dir_prod_mode(mock_strategy_prod_mode):
    """Tests that the state directory is correct in non-test mode."""
    state_dir = TradeState._get_state_dir(mock_strategy_prod_mode)
    assert "arbitrage_states_test" not in state_dir
    assert "arbitrage_states" in state_dir


def test_get_unfinished_trades_no_dir(mock_strategy):
    """Tests that get_unfinished_trades returns empty list if state dir does not exist."""
    state_dir = TradeState._get_state_dir(mock_strategy)
    if os.path.exists(state_dir):
        shutil.rmtree(state_dir)
    unfinished = TradeState.get_unfinished_trades(mock_strategy)
    assert unfinished == []


def test_get_unfinished_trades_malformed_json(trade_state_manager):
    """Tests that get_unfinished_trades handles malformed JSON files gracefully."""
    # Create a malformed json file
    with open(trade_state_manager.state_file_path, 'w') as f:
        f.write("{'invalid_json': ")

    # Create a valid state file to ensure the function continues
    valid_state = TradeState(trade_state_manager.strategy, 'valid_id')
    valid_state.save('valid_status', {'data': 'good'})

    # The new implementation should not raise an error, but log it and continue.
    unfinished = TradeState.get_unfinished_trades(trade_state_manager.strategy)

    assert len(unfinished) == 1
    assert unfinished[0]['check_id'] == 'valid_id'

    # Check that an error was logged for the corrupted file
    trade_state_manager.strategy.config_manager.general_log.error.assert_called_with(
        f"Corrupted state file found and skipped: {trade_state_manager.state_file_path}"
    )


def test_delete_non_existent_state(trade_state_manager):
    """Tests that deleting a non-existent state file does not raise an error."""
    assert not os.path.exists(trade_state_manager.state_file_path)
    try:
        trade_state_manager.delete()
    except Exception as e:
        pytest.fail(f"delete() raised an exception unexpectedly: {e}")


def test_archive_non_existent_state(trade_state_manager):
    """Tests that archiving a non-existent state file does not raise an error."""
    assert not os.path.exists(trade_state_manager.state_file_path)
    try:
        trade_state_manager.archive("test_reason")
    except Exception as e:
        pytest.fail(f"archive() raised an exception unexpectedly: {e}")
    archive_dir = os.path.join(trade_state_manager.state_dir, 'archive')
    assert not os.path.exists(archive_dir)
