import math
import os
import sys
import tkinter as tk

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui.components.data_panels import OrdersPanel, BalancesPanel


def test_column_sorting_logic():
    """Test the _get_sort_value method for robust column sorting."""
    # We need to import BaseDataPanel here to access its _get_sort_value method
    from gui.components.data_panels import BaseDataPanel

    # Create a dummy root and withdraw it to prevent a window from appearing
    root = tk.Tk()
    root.withdraw()

    # Create a dummy instance of BaseDataPanel to call the method
    # We don't need a full Tkinter setup for this unit test
    class MockDataPanel(BaseDataPanel):
        def __init__(self):
            # Mock columns, not used by _get_sort_value directly but required by BaseDataPanel init
            super().__init__(root, [('col1', 'Col1', 10)])

        def _redraw_tree(self):
            pass  # No UI redraw needed for this test

    mock_panel = MockDataPanel()
    get_sort_value = mock_panel._get_sort_value

    test_cases = [
        # Numerical values
        (10, (0, 10.0)),
        (-5, (0, -5.0)),
        (0.0, (0, 0.0)),
        ("123.45", (0, 123.45)),
        ("-98.76", (0, -98.76)),
        ("nan", (0, float('nan'))),
        ("inf", (0, float('inf'))),
        ("-inf", (0, float('-inf'))),
        ("$1,234.56", (0, 1234.56)),
        ("50%", (0, 50.0)),
        ("[100.00]", (0, 100.0)),
        ("1,000,000", (0, 1000000.0)),
        # None and empty strings
        (None, (0, float('-inf'))),
        ("", (0, float('-inf'))),
        ("None", (0, float('-inf'))),
        ("none", (0, float('-inf'))),
        (" NONE ", (0, float('-inf'))),
        # List values (for 'variation' column)
        ([0.001], (0, 0.001)),
        ([0.5], (0, 0.5)),
        ([], (0, float('-inf'))),  # Empty list
        # String values
        ("Apple", (1, "apple")),
        ("banana", (1, "banana")),
        ("Cherry", (1, "cherry")),
        ("N/A", (1, "n/a")),
        ("---", (1, "---")),
        ("Order1", (1, "order1")),
        ("Order10", (1, "order10")),
        ("Order2", (1, "order2")),
        ("BTC/USD", (1, "btc/usd")),
        ("PENDING", (1, "pending")),
    ]

    for value, expected_sort_key in test_cases:
        actual_sort_key = get_sort_value(value)
        # For NaN, we can't directly compare using ==, so check with math.isnan
        if isinstance(expected_sort_key[1], float) and math.isnan(expected_sort_key[1]):
            assert math.isnan(actual_sort_key[1]), f"For value '{value}', expected NaN but got {actual_sort_key[1]}"
            assert actual_sort_key[0] == expected_sort_key[
                0], f"For value '{value}', expected type {expected_sort_key[0]} but got {actual_sort_key[0]}"
        else:
            assert actual_sort_key == expected_sort_key, f"For value '{value}', expected {expected_sort_key} but got {actual_sort_key}"

    # Test overall sorting behavior with a list of mixed values
    mixed_data = [
        {'col': 'Order10'},
        {'col': 'Order2'},
        {'col': 100},
        {'col': 'N/A'},
        {'col': 50.5},
        {'col': None},
        {'col': '$1,000'},
        {'col': 'Order1'},
        {'col': ''},
        {'col': 'none'},
        {'col': [0.005]},
        {'col': [0.001]},
        {'col': 'Zebra'},
        {'col': 'apple'},
        {'col': '123.45'},
        {'col': '-50%'},
        {'col': []},  # Added for comprehensive test of empty list
    ]

    # Simulate sorting using the _get_sort_value key
    sorted_data = sorted(mixed_data, key=lambda k: get_sort_value(k.get('col', '')))

    # Expected order based on the _get_sort_value logic:
    # 1. float('-inf') values (None, '', 'none', [])
    # 2. Numerical values (sorted numerically)
    # 3. String values (sorted alphabetically, case-insensitive)

    # Extract the sort keys to verify the order
    actual_sort_keys = [get_sort_value(item.get('col')) for item in sorted_data]

    # Define the expected order of sort keys
    expected_sort_keys_order = [
        (0, float('-inf')),  # None
        (0, float('-inf')),  # ''
        (0, float('-inf')),  # 'none'
        (0, float('-inf')),  # []
        (0, -50.0),  # '-50%'
        (0, 0.001),  # [0.001]
        (0, 0.005),  # [0.005]
        (0, 50.5),  # 50.5
        (0, 100.0),  # 100
        (0, 123.45),  # '123.45'
        (0, 1000.0),  # '$1,000'
        (1, 'apple'),  # 'apple'
        (1, 'n/a'),  # 'N/A'
        (1, 'order1'),  # 'Order1'
        (1, 'order10'),  # 'Order10'
        (1, 'order2'),  # 'Order2'
        (1, 'zebra'),  # 'Zebra'
    ]

    # Compare the actual sorted keys with the expected sorted keys
    assert len(actual_sort_keys) == len(expected_sort_keys_order), \
        f"Length mismatch: Expected {len(expected_sort_keys_order)}, Got {len(actual_sort_keys)}"

    for i, (actual_key, expected_key) in enumerate(zip(actual_sort_keys, expected_sort_keys_order)):
        # For float values, direct comparison is fine unless NaN is involved.
        # For string values, direct comparison is fine.
        assert actual_key == expected_key, \
            f"Mismatch at index {i}: Expected {expected_key}, Got {actual_key}"

    # Test descending order
    sorted_data_desc = sorted(mixed_data, key=lambda k: get_sort_value(k.get('col', '')), reverse=True)
    actual_sort_keys_desc = [get_sort_value(item.get('col')) for item in sorted_data_desc]
    expected_sort_keys_order_desc = list(reversed(expected_sort_keys_order))

    assert len(actual_sort_keys_desc) == len(expected_sort_keys_order_desc), \
        f"Descending Length mismatch: Expected {len(expected_sort_keys_order_desc)}, Got {len(actual_sort_keys_desc)}"

    for i, (actual_key, expected_key) in enumerate(zip(actual_sort_keys_desc, expected_sort_keys_order_desc)):
        assert actual_key == expected_key, \
            f"Descending Mismatch at index {i}: Expected {expected_key}, Got {actual_key}"

    # Clean up the dummy root window
    root.destroy()


@pytest.fixture
def tk_root_for_panels():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_orders_panel_empty_data(tk_root_for_panels):
    """Tests that the OrdersPanel handles an empty data update gracefully."""
    panel = OrdersPanel(tk_root_for_panels)
    try:
        panel.update_data([])
        # Process the update queue directly to avoid mainloop
        panel._process_updates()
        tk_root_for_panels.update_idletasks()
        assert not panel.tree.get_children()
    except Exception as e:
        pytest.fail(f"OrdersPanel failed to handle empty data: {e}")


def test_balances_panel_empty_data(tk_root_for_panels):
    """Tests that the BalancesPanel handles an empty data update gracefully."""
    panel = BalancesPanel(tk_root_for_panels)
    try:
        panel.update_data([])
        # Process the update queue directly to avoid mainloop
        panel._process_updates()
        tk_root_for_panels.update_idletasks()
        assert not panel.tree.get_children()
    except Exception as e:
        pytest.fail(f"BalancesPanel failed to handle empty data: {e}")


def test_balances_panel_total_usd_calculation(tk_root_for_panels):
    """Tests that BalancesPanel._safe_update correctly calculates total_usd."""
    panel = BalancesPanel(tk_root_for_panels)
    sample_data = [
        {'symbol': 'BTC', 'usd_price': 50000.0, 'total': 0.5, 'free': 0.4},
        {'symbol': 'LTC', 'usd_price': 150.0, 'total': 10, 'free': 8},
        {'symbol': 'NOCEX', 'total': 20, 'free': 20}  # Missing usd_price
    ]

    # Call the update method, which queues the data
    panel.update_data(sample_data)
    # Manually process the update queue to bypass the `after` scheduler for testing
    panel._process_updates()

    # The data is now in current_data, sorted. Let's find our items.
    btc_data = next((item for item in panel.current_data if item['symbol'] == 'BTC'), None)
    ltc_data = next((item for item in panel.current_data if item['symbol'] == 'LTC'), None)
    nocex_data = next((item for item in panel.current_data if item['symbol'] == 'NOCEX'), None)

    assert btc_data is not None
    assert 'total_usd' in btc_data
    assert btc_data['total_usd'] == pytest.approx(25000.0)

    assert ltc_data is not None
    assert 'total_usd' in ltc_data
    assert ltc_data['total_usd'] == pytest.approx(1500.0)

    assert nocex_data is not None
    assert 'total_usd' in nocex_data
    assert nocex_data['total_usd'] == 0.0  # Should default to 0
