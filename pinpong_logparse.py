import ast
import re
from datetime import datetime

from tabulate import tabulate


def reverse_side(s):
    if s == "BUY":
        return "SELL"
    elif s == "SELL":
        return "BUY"


def read_log_file(log_file_path):
    orders = {}  # Track orders by ID
    finished_orders = []  # Track order finish events
    current_order_details = []

    with open(log_file_path, 'r') as file:
        for line in file:
            line = line.strip()
            if not line:
                continue

            # Extract timestamp from all lines
            timestamp_match = re.search(r'\[(.*?)\]', line)
            timestamp = timestamp_match.group(1) if timestamp_match else ''

            # Look for order finished events
            if 'order FINISHED:' in line:
                name_match = re.search(r"'name': '([^']+)'", line)
                orderid_match = re.search(r"'orderid': '([^']+)'", line)
                side_match = re.search(r"'side': '([^']+)'", line)

                if name_match and orderid_match and side_match:
                    current_order_id = orderid_match.group(1)
                    pair_match = re.search(r"'pair': '([^']+)'", line)
                    finished_orders.append({
                        'timestamp': timestamp,
                        'name': name_match.group(1),
                        'orderid': current_order_id,
                        'side': side_match.group(1),
                        'pair': pair_match.group(1) if pair_match else ''
                    })
                    current_order_details = []
                continue

            # Collect order details lines
            if current_order_id in line and 'id' in line and 'status' in line:
                try:
                    # Extract the dictionary part
                    dict_str = re.search(r'INFO - (\{.*})', line).group(1)
                    # Use ast.literal_eval to safely parse the dictionary string
                    order_data = ast.literal_eval(dict_str)
                    current_order_details.append(order_data)
                except (AttributeError, SyntaxError, ValueError):
                    continue

            # Process order when we have any details
            if current_order_id and current_order_details:
                # Merge all order details
                merged_order = {
                    'timestamp': timestamp,
                    'side': next((f['side'] for f in finished_orders if f['orderid'] == current_order_id), ''),
                    'name': next((f['name'] for f in finished_orders if f['orderid'] == current_order_id), ''),
                    # Get pair/symbol information separately
                    'pair': next((f.get('pair', '') for f in finished_orders if f['orderid'] == current_order_id), '')
                }
                for detail in current_order_details:
                    merged_order.update(detail)

                # Convert numeric fields
                for field in ['maker_size', 'taker_size']:
                    if field in merged_order:
                        if isinstance(merged_order[field], str):
                            merged_order[field] = float(merged_order[field].replace(',', ''))
                        else:
                            merged_order[field] = float(merged_order[field])

                orders[current_order_id] = merged_order
                current_order_id = None
                current_order_details = []

    # Group orders by pair name
    result = {}
    for order_id, order in orders.items():
        name = order.get('name', '')
        if name not in result:
            result[name] = []
        result[name].append(order)

    return result


def calculate_profit(trades):
    completed_pingpong = {}
    inprogress_pingpong = {}
    profit_pingpong = {}
    last_sell = {}

    # Process each trading sequence independently by name
    for sequence_name, orders in trades.items():
        # Sort orders by timestamp
        sorted_orders = sorted(orders, key=lambda x: x['timestamp'])

        for order in sorted_orders:
            symbol = order.get('symbol', '')  # Get symbol from order data
            side = order['side']
            maker = order['maker']
            maker_size = order['maker_size']
            taker = order['taker']
            taker_size = order['taker_size']

            if side == 'SELL':
                # Track last sell order for this pair name
                last_sell[sequence_name] = order

            elif side == 'BUY':
                if sequence_name in last_sell:
                    sell_order = last_sell.pop(sequence_name)
                    # Calculate profit in the acquired asset (what we received from SELL)
                    profit_asset = sell_order['taker']
                    sell_amount = float(sell_order['taker_size'])
                    buy_amount = float(order['maker_size'])  # Amount we paid in the buy
                    profit = float(f"{sell_amount - buy_amount:.6f}")

                    if sequence_name not in completed_pingpong:
                        completed_pingpong[sequence_name] = []
                    completed_pingpong[sequence_name].append([
                        sell_order,
                        order,
                        f"{profit} {profit_asset}"
                    ])

                    # Store profit info by sequence name with pair information
                    if sequence_name not in profit_pingpong:
                        profit_pingpong[sequence_name] = {
                            'profit': profit,
                            'asset': profit_asset,
                            'pair': order.get('pair', '')
                        }
                    else:
                        # Only accumulate profit if it's positive
                        if profit > 0:
                            profit_pingpong[sequence_name]['profit'] += profit
                            profit_pingpong[sequence_name]['profit'] = float(
                                f"{profit_pingpong[sequence_name]['profit']:.6f}"
                            )
                            # Update pair if missing
                            if not profit_pingpong[sequence_name]['pair']:
                                profit_pingpong[sequence_name]['pair'] = order.get('pair', '')

        # Check for any remaining unpaired sell orders from this sequence only
        if sequence_name in last_sell:
            sell_order = last_sell.pop(sequence_name)
            if sequence_name not in inprogress_pingpong:
                inprogress_pingpong[sequence_name] = []
            # Add formatted order with explicit fields
            inprogress_pingpong[sequence_name].append({
                'name': sell_order.get('name', sequence_name),
                'pair': sell_order.get('pair', ''),
                'timestamp': sell_order.get('timestamp', ''),
                'side': sell_order.get('side', ''),
                'maker': sell_order.get('maker', ''),
                'maker_size': float(sell_order.get('maker_size', 0)),
                'taker': sell_order.get('taker', ''),
                'taker_size': float(sell_order.get('taker_size', 0))
            })

    return completed_pingpong, inprogress_pingpong, profit_pingpong


# Define a custom sorting key function
def custom_sort(item):
    return (item[0], item[1])  # Assuming the first item is the symbol and the second is the timestamp


def display_results(completed_pingpong, inprogress_pingpong, profit_pingpong):
    completed_table_data = []
    profit_table_data = []

    if completed_pingpong:
        for name, sequences in completed_pingpong.items():
            for sequence in sequences:
                sell_order, buy_order, profit = sequence

            # Process sell order
            sell_timestamp = sell_order['timestamp']
            completed_table_data.append((
                sell_order['name'],
                sell_order.get('pair', ''),
                sell_timestamp,
                'SELL',
                float(sell_order['maker_size']),
                sell_order['maker'],
                'BUY',
                float(sell_order['taker_size']),
                sell_order['taker'],
                "",
                ""
            ))

            # Process buy order with time delta and profit
            buy_timestamp = buy_order['timestamp']

            date_format = "%Y-%m-%d %H:%M:%S,%f"
            date1 = datetime.strptime(sell_timestamp, date_format)
            date2 = datetime.strptime(buy_timestamp, date_format)
            delta_date = str(date2 - date1).split('.')[0]

            completed_table_data.append((
                buy_order['name'],
                buy_order.get('pair', ''),
                buy_timestamp,
                'BUY',
                float(buy_order['maker_size']),
                buy_order['maker'],
                'SELL',
                float(buy_order['taker_size']),
                buy_order['taker'],
                profit,
                delta_date
            ))

    # Ensure the headers match the number of columns in the completed_table_data
    headers = ["Name", "Symbol", "Timestamp", "Side", "Size T1", "Token1", "R_Side", "Size T2", "Token2", "Profit",
               "Exec time (D, h:m:s)"]

    # Only print if there's data to display
    if completed_table_data:
        # Print the tabulated data with numeric columns right-aligned
        colalign = ("left", "left", "left", "left", "right", "left", "left", "right", "left", "right", "right")
        print(tabulate(completed_table_data, headers=headers, tablefmt="pretty", colalign=colalign))
    else:
        print("\nNo completed pingpong trades to display")

    if inprogress_pingpong:
        print("\nInprogress Pingpong:")
        # Convert the dictionary to a list of tuples for tabulation
        inprogress_table_data = []
        for name, orders in inprogress_pingpong.items():
            for order in orders:
                inprogress_table_data.append((
                    order.get('name', name),  # Use order name first, fallback to dict key
                    order.get('pair', ''),
                    order.get('timestamp', ''),
                    order.get('side', ''),
                    float(order.get('maker_size', 0)),
                    order.get('maker', ''),
                    reverse_side(order.get('side', '')),
                    float(order.get('taker_size', 0)),
                    order.get('taker', '')
                ))
    if inprogress_table_data:
        sorted_inprogress = sorted(inprogress_table_data, key=custom_sort)
        # Print the tabulated data with numeric columns right-aligned
        headers = ["Name", "Symbol", "Timestamp", "Side", "Size T1", "Token1", "R_Side", "Size T2", "Token2"]
        colalign = ["left"] * len(headers)
        # Set right alignment for numeric columns (Size T1 and Size T2)
        colalign[headers.index("Size T1")] = "right"
        colalign[headers.index("Size T2")] = "right"
        print(tabulate(sorted_inprogress, headers=headers, tablefmt="pretty", colalign=colalign))
    else:
        print("\nNo in-progress pingpong trades to display")

    if profit_pingpong:
        print("\nProfit Pingpong:")
        # Convert the dictionary to a list of tuples for tabulation with profit values flattened
        profit_table_data = []
        for name, profit_info in profit_pingpong.items():
            profit_table_data.append((
                name,
                profit_info.get('pair', ''),  # Use pair if available
                profit_info['profit'],
                profit_info['asset']
            ))

    if profit_table_data:
        # Print the tabulated data
        colalign = ("left", "left", "right", "left")
        print(tabulate(profit_table_data, headers=["Name", "Symbol", "Profit", "Asset"], tablefmt="pretty",
                       colalign=colalign))
    else:
        print("\nNo profit data to display")


def main():
    # Specify the path to your log file
    log_file_path = "logs/pingpong_trade.log"

    trades = read_log_file(log_file_path)
    completed_pingpong, inprogress_pingpong, profit_pingpong = calculate_profit(trades)

    # Display your results or use them as needed
    display_results(completed_pingpong, inprogress_pingpong, profit_pingpong)


if __name__ == "__main__":
    main()
