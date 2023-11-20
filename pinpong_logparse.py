import re
import json
from tabulate import tabulate
from datetime import datetime


def reverse_side(s):
    if s == "BUY":
        return "SELL"
    elif s == "SELL":
        return "BUY"


def read_log_file(log_file_path):
    result = {}

    with open(log_file_path, 'r') as file:
        log_data = file.read()

    log_lines = log_data.strip().split("\n")

    for log_line in log_lines:
        if "'symbol':" in log_line:
            match = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\] INFO - (\{[^}]+\})", log_line)
            if match:
                timestamp, json_part = match.groups()
                json_part = json_part.replace("False", '"FALSE"')
                json_part = json_part.replace("True", '"TRUE"')
                json_part = json_part.replace("'", '"')
                myjson = json.loads(json_part)

                if myjson:
                    if not myjson['symbol'] in result:
                        result[myjson['symbol']] = []
                    maker = myjson['maker']
                    maker_size = float(f"{myjson['maker_size']:.6f}")
                    taker = myjson['taker']
                    taker_size = float(f"{myjson['taker_size']:.6f}")
                    if myjson['side'] == "BUY":
                        # reverse maker/taker for reading logic
                        buff = maker
                        buff_size = maker_size
                        maker = taker
                        maker_size = taker_size
                        taker = buff
                        taker_size = buff_size
                    # store result
                    result[myjson['symbol']].append([
                        timestamp,
                        myjson['side'],
                        maker_size,
                        maker,
                        reverse_side(myjson['side']),
                        taker_size,
                        taker
                    ])

    return result


def calculate_profit(trades):
    asset_count_buy = {}
    asset_count_sell = {}
    last_sell_trade = None
    inprogress_pingpong = {}
    completed_pingpong = {}
    profit_pingpong = {}

    for symbol, trades in trades.items():
        for trade in trades:
            timestamp, side, maker_size, maker, reverse_side, taker_size, taker = trade

            if side == 'SELL':
                # Update 'SELL' side asset count
                if taker not in asset_count_sell:
                    asset_count_sell[taker] = 0.0
                asset_count_sell[taker] += taker_size

                # Keep track of the last 'SELL' trade
                last_sell_trade = trade

            elif side == 'BUY':
                # Update 'BUY' side asset count
                if maker not in asset_count_buy:
                    asset_count_buy[maker] = 0.0
                asset_count_buy[maker] += maker_size
                # Check if there was a previous 'SELL' trade
                if last_sell_trade:
                    _, _, _, _, _, last_taker_size, last_taker = last_sell_trade
                    # Calculate profit using the last 'SELL' and current 'BUY' trades
                    profit = float(f"{last_taker_size - taker_size:.6f}")
                    if symbol not in completed_pingpong:
                        completed_pingpong[symbol] = []
                    completed_pingpong[symbol].append([last_sell_trade, trade, f"{profit} {taker}"])
                    if symbol not in profit_pingpong:
                        profit_pingpong[symbol] = {'profit': profit, 'asset': taker}
                    else:
                        profit_pingpong[symbol]['profit'] += profit
                        profit_pingpong[symbol]['profit'] = float(f"{profit_pingpong[symbol]['profit']:.6f}")
                    last_sell_trade = None

        # Check if there was a 'SELL' without a subsequent 'BUY'
        if last_sell_trade:
            if symbol not in inprogress_pingpong:
                inprogress_pingpong[symbol] = []
            inprogress_pingpong[symbol].append(last_sell_trade)

    return completed_pingpong, inprogress_pingpong, profit_pingpong


def display_results(completed_pingpong, inprogress_pingpong, profit_pingpong):
    # Convert the dictionary to a list of tuples for tabulation with trades flattened
    completed_table_data = []

    for symbol, trades_list in completed_pingpong.items():
        for pingpongsequence in trades_list:
            for event in pingpongsequence:
                # 1PING #2PONG #3PROFIT
                if isinstance(event, list):
                    timestamp, side, maker_size, maker, r_side, taker_size, taker = event
                    if side == "BUY":
                        # Example dates
                        date_str1 = pingpongsequence[0][0]
                        date_str2 = pingpongsequence[1][0]

                        # Convert string representations to datetime objects
                        date_format = "%Y-%m-%d %H:%M:%S,%f"
                        date1 = datetime.strptime(date_str1, date_format)
                        date2 = datetime.strptime(date_str2, date_format)

                        delta_date = str(date2 - date1).split('.')[0]

                        # Assuming profit is the third element in the pingpongsequence
                        profit = pingpongsequence[2]
                    else:
                        delta_date = ""
                        profit = ""

                    completed_table_data.append(
                        (symbol, timestamp, side, maker_size, maker, r_side, taker_size, taker, profit, delta_date))

    # Ensure the headers match the number of columns in the completed_table_data
    headers = ["Symbol", "Timestamp", "Side", "Size T1", "Token1", "R_Side", "Size T2", "Token2", "Profit",
               "Exec time (D, h:m:s)"]

    # Print the tabulated data with left-aligned content
    print(tabulate(completed_table_data, headers=headers, tablefmt="pretty"))

    print("\nInprogress Pingpong:")
    # Convert the dictionary to a list of tuples for tabulation with trades flattened
    inprogress_table_data = [(symbol, *trade) for symbol, trades in inprogress_pingpong.items() for trade in trades]

    # Print the tabulated data
    print(tabulate(inprogress_table_data,
                   headers=["Symbol", "Timestamp", "Side""Size T1", "Token1", "R_Side", "Size T2",
                            "Token2"],
                   tablefmt="pretty"))

    print("\nProfit Pingpong:")
    # Convert the dictionary to a list of tuples for tabulation with profit values flattened
    profit_table_data = [(symbol, profit['profit'], profit['asset']) for symbol, profit in profit_pingpong.items()]

    # Print the tabulated data
    print(tabulate(profit_table_data, headers=["Symbol", "Profit", "Asset"], tablefmt="pretty"))


def main():
    # Specify the path to your log file
    log_file_path = "logs/pingpong_trade.log"

    trades = read_log_file(log_file_path)
    completed_pingpong, inprogress_pingpong, profit_pingpong = calculate_profit(trades)

    # Display your results or use them as needed
    display_results(completed_pingpong, inprogress_pingpong, profit_pingpong)


if __name__ == "__main__":
    main()
