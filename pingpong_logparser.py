import ast
import logging
import re
from collections import defaultdict
from datetime import datetime

from tabulate import tabulate

from definitions.logger import setup_logging

logger = setup_logging(name="GENERAL_LOG",
                       level=logging.INFO,
                       console=True)


def reverse_side(s):
    if s == "BUY":
        return "SELL"
    elif s == "SELL":
        return "BUY"


def extract_dict_from_line(line):
    match = re.search(r'\{.*\}', line)
    if match:
        try:
            return ast.literal_eval(match.group(0))
        except Exception:
            pass
    return None


def read_log_file(log_file_path):
    finished_orders = defaultdict(list)
    xbridge_orders = defaultdict(list)

    with open(log_file_path, 'r') as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            dict_in_line = extract_dict_from_line(line)
            if "order FINISHED:" in line:
                name = dict_in_line["name"]
                finished_orders[name].append(dict_in_line)
            elif "virtual order:" in line:
                pass  # we don't need this line
            elif "xbridge order:" in line:
                id = dict_in_line["id"]
                xbridge_orders[id].append(dict_in_line)
            else:
                logger.warning(f"Failed to identify log line ? {line}")

    return finished_orders, xbridge_orders


def main():
    # Specify the path to your log file
    log_file_path = "logs/pingpong_trade.log"
    logger.info(log_file_path)
    finished_orders, xbridge_orders = read_log_file(log_file_path)

    completed_table_data = []
    inprogress_table_data = []
    profit_info = defaultdict(lambda: {'total_profit': 0.0, 'asset': None})

    for instance_name, orders_list in finished_orders.items():
        completed_cycles = []
        in_progress_cycle = []
        current_sell = None
        current_buy = None

        for order in orders_list:
            if order['side'] == 'SELL':
                id = order['orderid']
                xbridge_order = xbridge_orders.get(id)[0]
                xbridge_order['instance_name'] = instance_name
                current_sell = xbridge_order
                current_buy = None
            elif order['side'] == 'BUY' and current_sell is not None:
                id = order['orderid']
                current_buy = xbridge_orders.get(id)[0]
                current_buy['instance_name'] = instance_name
                completed_cycles.append((current_sell, current_buy))
                current_sell = None  # reset after pairing.

        if current_sell and not current_buy:
            in_progress_cycle.append(current_sell)

        for sell_order, buy_order in completed_cycles:
            instance_name = sell_order.get('instance_name', '')

            # Row for SELL part
            symbol_sell = f"{sell_order['maker']}/{sell_order['taker']}"
            row1 = [
                instance_name,
                symbol_sell,
                sell_order.get('created_at', ''),
                'SELL',
                float(sell_order['maker_size']),
                sell_order['maker'],
                reverse_side('SELL'),  # 'BUY'
                float(sell_order['taker_size']),
                sell_order['taker'],
                "", ""  # Profit and Exec Time (empty for first row)
            ]

            # Row for BUY part
            symbol_buy = f"{buy_order['maker']}/{buy_order['taker']}"
            try:
                sell_time = datetime.strptime(sell_order.get('updated_at', '1970-01-01T00:00:00Z'),
                                              "%Y-%m-%dT%H:%M:%S.%fZ")
                buy_time = datetime.strptime(buy_order.get('created_at', '1970-01-01T00:00:00Z'),
                                             "%Y-%m-%dT%H:%M:%S.%fZ")
                delta = buy_time - sell_time
                delta_str = f"{delta.days} days {delta.seconds // 3600}:{(delta.seconds // 60) % 60}:{delta.seconds % 60}"
            except:
                delta_str = ""

            profit = float(sell_order['taker_size']) - float(buy_order['maker_size'])
            row2 = [
                instance_name,
                symbol_buy,
                buy_order.get('created_at', ''),
                'BUY',
                float(buy_order['maker_size']),
                buy_order['maker'],
                reverse_side('BUY'),  # 'SELL'
                float(buy_order['taker_size']),
                buy_order['taker'],
                f"{profit:.6f} {buy_order['maker']}",
                delta_str
            ]

            completed_table_data.extend([row1, row2])

            # Update profit_info with instance_name as key
            profit_info[instance_name]['total_profit'] += profit
            profit_info[instance_name]['asset'] = buy_order['maker']

        for sell in in_progress_cycle:
            instance_name = sell.get('instance_name', '')
            symbol = f"{sell['maker']}/{sell['taker']}"

            row = [
                instance_name,
                symbol,
                sell.get('created_at', ''),
                'SELL',
                float(sell['maker_size']),
                sell['maker'],
                reverse_side('SELL'),  # 'BUY'
                float(sell['taker_size']),
                sell['taker']
            ]

            inprogress_table_data.append(row)

    # Display Completed Cycles Table
    if completed_table_data:
        headers = [
            "Name", "Symbol", "Timestamp",
            "Side", "Size T1", "Token1",
            "R_Side", "Size T2", "Token2",
            "Profit", "Exec time (D, h:m:s)"
        ]
        colalign = ("left", "left", "left", "left", "right", "left", "left", "right", "left", "right", "right")
        print("\nCompleted Traded:")
        print(tabulate(completed_table_data, headers=headers, tablefmt="pretty", colalign=colalign))
    else:
        logger.info("No completed cycles found")

    # Display In-Progress Cycles
    if inprogress_table_data:
        in_progress_headers = [
            "Name", "Symbol", "Timestamp",
            "Side", "Size T1", "Token1",
            "R_Side", "Size T2", "Token2"
        ]
        colalign_inprog = ["left"] * len(in_progress_headers)
        colalign_inprog[in_progress_headers.index("Size T1")] = "right"
        colalign_inprog[in_progress_headers.index("Size T2")] = "right"
        print("\nIn-Progress Cycles:")
        print(tabulate(inprogress_table_data, headers=in_progress_headers, tablefmt="pretty", colalign=colalign_inprog))
    else:
        logger.info("No in-progress cycles found")

    # Display Profit Summary
    profit_table_data = []
    for instance_name, data in profit_info.items():
        total_profit = data['total_profit']
        asset = data['asset']
        profit_table_data.append(
            (instance_name, f"{total_profit:.6f}", asset or "")
        )

    if profit_table_data:
        print("\nProfit Summary:")
        print(tabulate(profit_table_data, headers=["Instance", "Total Profit", "Asset"], tablefmt="pretty",
                       colalign=("left", "right", "left")))
    else:
        logger.info("No profit data available")


if __name__ == "__main__":
    main()
