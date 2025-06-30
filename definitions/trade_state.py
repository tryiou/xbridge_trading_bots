import json
import os
import shutil
import time
from typing import List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from strategies.arbitrage_strategy import ArbitrageStrategy


class TradeState:
    """Manages the state of an arbitrage trade for persistence and recovery."""

    def __init__(self, strategy: 'ArbitrageStrategy', check_id: str):
        self.strategy = strategy
        self.config_manager = strategy.config_manager
        self.check_id = check_id
        self.log_prefix = self.check_id if self.strategy.test_mode else self.check_id[:8]
        self.state_dir = self._get_state_dir(self.strategy)
        os.makedirs(self.state_dir, exist_ok=True)
        self.state_file_path = os.path.join(self.state_dir, f"{self.check_id}.json")
        self.state_data = {'check_id': self.check_id}

    @staticmethod
    def _get_state_dir(strategy: 'ArbitrageStrategy') -> str:
        """Determines the state directory based on whether the strategy is in test mode."""
        dir_name = "arbitrage_states_test" if strategy.test_mode else "arbitrage_states"
        return os.path.join(strategy.config_manager.ROOT_DIR, "data", dir_name)

    def save(self, status: str, data: Dict[str, Any]):
        """Saves the current state to a file."""
        self.state_data['status'] = status
        self.state_data['timestamp'] = time.time()
        self.state_data.update(data)
        try:
            with open(self.state_file_path, 'w') as f:
                json.dump(self.state_data, f, indent=4)
            self.config_manager.general_log.debug(
                f"[{self.log_prefix}] Saved state '{status}' to {self.state_file_path}")
        except Exception as e:
            self.config_manager.general_log.error(f"[{self.log_prefix}] Failed to save state: {e}")

    def delete(self):
        """Deletes the state file upon successful completion."""
        if os.path.exists(self.state_file_path):
            os.remove(self.state_file_path)
            self.config_manager.general_log.info(f"[{self.log_prefix}] Trade complete. Removed state file.")

    def archive(self, reason: str):
        """Archives the state file for manual review."""
        if not os.path.exists(self.state_file_path):
            return
        archive_dir = os.path.join(self.state_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)
        archive_filename = f"{self.check_id}-{reason}-{int(time.time())}.json"
        archive_path = os.path.join(archive_dir, archive_filename)
        try:
            shutil.move(self.state_file_path, archive_path)
            self.config_manager.general_log.warning(f"[{self.log_prefix}] Archived state file to {archive_path}")
        except OSError as e:
            self.config_manager.general_log.error(f"[{self.log_prefix}] Failed to archive state file: {e}")

    @classmethod
    def get_unfinished_trades(cls, strategy: 'ArbitrageStrategy') -> List[Dict[str, Any]]:
        """Scans for and loads any unfinished trade states."""
        state_dir = cls._get_state_dir(strategy)
        if not os.path.exists(state_dir):
            return []
        unfinished_files = [os.path.join(state_dir, f) for f in os.listdir(state_dir) if f.endswith('.json')]
        return [json.load(open(f)) for f in unfinished_files]

    @classmethod
    def cleanup_all_states(cls, strategy: 'ArbitrageStrategy'):
        """For testing purposes, clears all active and archived states."""
        state_dir = cls._get_state_dir(strategy)
        if os.path.exists(state_dir):
            shutil.rmtree(state_dir)
        os.makedirs(state_dir, exist_ok=True)
        strategy.config_manager.general_log.info("Cleaned up all trade states for testing.")
