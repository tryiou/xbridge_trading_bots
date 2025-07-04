import logging
import os
import sys
import tkinter as tk
import traceback
from contextlib import contextmanager
from tkinter import ttk
from typing import List, Dict, Any
from unittest.mock import patch

# We need to set the GUI mode before importing the GUI class
# to prevent logging setup issues during tests.
from definitions.bcolors import bcolors
from definitions.logger import set_gui_mode
from definitions.yaml_mix import YamlToObject

set_gui_mode(True)

from gui.gui import GUI_Main


class GUITester:
    """
    A dedicated class to test the GUI application's initialization and
    component states, following the project's custom tester pattern.
    """

    def __init__(self):
        self.test_results: List[Dict[str, Any]] = []

    def run_all_tests(self):
        """Runs the full suite of GUI tests."""
        print("--- Starting GUI Test Suite ---")
        try:
            self._test_initialization()
            self._test_start_stop_button_initial_state()
        except Exception as e:
            print(f"A critical error occurred during the test suite run: {e}")
            traceback.print_exc()
        finally:
            print("\n--- GUI Test Suite Finished ---")
            self._print_summary()

    def _print_summary(self):
        """Prints a formatted summary of the test suite results."""
        summary_lines = [
            "\n" + "=" * 60,
            "--- Test Suite Summary ---".center(60),
            "=" * 60
        ]
        passed_count = 0
        failed_count = 0

        for result in self.test_results:
            status = f"{bcolors.OKGREEN}PASSED{bcolors.ENDC}" if result['passed'] else f"{bcolors.FAIL}FAILED{bcolors.ENDC}"
            summary_lines.append(f"  - [{status}] {result['name']}")
            if result['passed']:
                passed_count += 1
            else:
                failed_count += 1

        summary_lines.append("-" * 60)
        summary_lines.append(f"Total Tests: {len(self.test_results)} | Passed: {passed_count} | Failed: {failed_count}")
        summary_lines.append("=" * 60)
        print("\n".join(summary_lines))

    @contextmanager
    def _patch_and_setup_app(self):
        """A context manager to patch dependencies and manage the app lifecycle for a single test."""
        # Store original stdout/stderr to restore them later. This is crucial to prevent
        # the test runner's print statements from trying to write to a destroyed widget.
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        # Mock external dependencies that could block or have side effects
        patchers = []

        # 1. Mock RPC detection to avoid file system access and potential user prompts
        patchers.append(patch('definitions.detect_rpc.detect_rpc', return_value=('user', 'port', 'pass', '/fake/dir')))

        # 2. Mock config file creation and loading
        patchers.append(patch('definitions.config_manager.ConfigManager.create_configs_from_templates', return_value=None))

        # Mock _load_and_update_config to return a mock config object
        # This prevents file I/O and gives us control over config values.
        mock_config_data = {
            'config_pingpong': {'debug_level': 2, 'ttk_theme': 'darkly', 'pair_configs': [{'name': 'test', 'pair': 'LTC/DOGE', 'enabled': True}]},
            'config_basicseller': {'seller_configs': [{'name': 'test', 'pair': 'LTC/DOGE', 'enabled': True}]},
            'config_arbitrage': {'trading_tokens': ['LTC', 'DOGE'], 'fee_token': 'BLOCK'},
            'config_xbridge': {
                'debug_level': 1,
                'max_concurrent_tasks': 5,
                'taker_fee_block': 0.015,
                'taker_fee_btc': 0.0001,
                'monitoring': {'timeout': 300, 'poll_interval': 15}
            },
            'config_ccxt': {'ccxt_exchange': 'kucoin', 'ccxt_hostname': None, 'debug_level': 1},
            'config_coins': {'usd_ticker_custom': {}},
            'config_thorchain': {}
        }
        patchers.append(patch('definitions.config_manager.ConfigManager._load_and_update_config',
                              side_effect=lambda name: YamlToObject(mock_config_data.get(name.replace('.yaml', ''), {}))))

        # 3. Mock CCXT initialization to avoid network calls
        patchers.append(patch('definitions.ccxt_manager.CCXTManager.init_ccxt_instance'))

        # 4. Mock file writing in the logger
        # The FileHandler needs to be mocked with an instance that has a `level`
        # attribute to avoid a TypeError during logging calls in the GUI init.
        mock_fh_instance = logging.FileHandler(os.devnull)  # Use a real handler writing to null device
        patchers.append(patch('logging.FileHandler', return_value=mock_fh_instance))
        patchers.append(patch('os.makedirs'))

        # Start all patchers
        for p in patchers:
            p.start()

        app = None
        try:
            # Create the GUI application instance for the test
            app = GUI_Main()
            app.root.update()  # Process initial events
            yield app
        finally:
            # Stop any pending refresh loops and unbind events first.
            if app and app.root and app.root.winfo_exists():
                # Unbind the notebook tab change event to prevent it from firing during teardown.
                if app.notebook.winfo_exists():
                    app.notebook.unbind("<<NotebookTabChanged>>")

                for frame in app.strategy_frames.values():
                    if frame.winfo_exists():
                        frame.stop_refresh()
                        # Call cleanup to unbind events like <Configure>
                        frame.cleanup()

            # Restore stdout and stderr to prevent print statements from causing errors.
            sys.stdout = original_stdout
            sys.stderr = original_stderr

            # Clear logging handlers to prevent them from trying to write to a destroyed widget.
            logging.getLogger().handlers.clear()

            # Process any final pending events and then destroy the window.
            if app and app.root and app.root.winfo_exists():
                app.root.update()
                app.root.destroy()

            # Finally, stop all patchers.
            for p in reversed(patchers):
                p.stop()

    def _test_initialization(self):
        """Test if the main GUI window and its components initialize correctly."""
        test_name = "GUI Initialization"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            with self._patch_and_setup_app() as app:
                assert isinstance(app.root, tk.Tk), "Root window is not a Tk instance"
                assert app.root.title() == "XBridge Trading Bots", "Window title is incorrect"

                # Check if the notebook and tabs were created
                assert isinstance(app.notebook, ttk.Notebook), "Notebook widget not created"
                tabs = app.notebook.tabs()
                tab_texts = [app.notebook.tab(tab, "text") for tab in tabs]

                assert 'PingPong' in tab_texts, "PingPong tab is missing"
                assert 'Basic Seller' in tab_texts, "Basic Seller tab is missing"
                assert 'Arbitrage' in tab_texts, "Arbitrage tab is missing"
                assert 'Logs' in tab_texts, "Logs tab is missing"
                print("[TEST PASSED] GUI initialized correctly with all expected tabs.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    def _test_start_stop_button_initial_state(self):
        """Test the initial state of START/STOP buttons in the PingPong frame."""
        test_name = "Initial Button State"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            with self._patch_and_setup_app() as app:
                pingpong_frame = app.strategy_frames.get('PingPong')
                assert pingpong_frame is not None, "PingPong frame not found"

                # In the initial state, START should be enabled and STOP should be disabled.
                assert str(pingpong_frame.btn_start['state']) == 'normal', "START button should be normal"
                assert str(pingpong_frame.btn_stop['state']) == 'disabled', "STOP button should be disabled"
                assert str(pingpong_frame.btn_configure['state']) == 'normal', "CONFIGURE button should be normal"
                print("[TEST PASSED] Initial button states are correct.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})