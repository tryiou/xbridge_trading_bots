import re
import json
from tabulate import tabulate
from datetime import datetime, timedelta
import argparse


def reverse_side(s):
    if s == "BUY":
        return "SELL"
    elif s == "SELL":
        return "BUY"


def read_log_file(log_file_path, timeframe=None):
    result = {}
    now = datetime.now()

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
                    trade_datetime = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f")
                    if timeframe is not None and now - trade_datetime >= timeframe:
                        continue

                    if not myjson['symbol'] in result:
                        result[myjson['symbol']] = []
                    maker = myjson['maker']
                    maker_size = float(f"{myjson['maker_size']:.6f}")
                    taker = myjson['taker']
                    taker_size = float(f"{myjson['taker_size']:.6f}")
                    if myjson['side'] == "BUY":
                        buff = maker
                        buff_size = maker_size
                        maker = taker
                        maker_size = taker_size
                        taker = buff
                        taker_size = buff_size
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
                if taker not in asset_count_sell:
                    asset_count_sell[taker] = 0.0
                asset_count_sell[taker] += taker_size

                last_sell_trade = trade

            elif side == 'BUY':
                if maker not in asset_count_buy:
                    asset_count_buy[maker] = 0.0
                asset_count_buy[maker] += maker_size

                if last_sell_trade:
                    _, _, last_maker_size, last_maker, _, last_taker_size, last_taker = last_sell_trade
                    # print(last_maker_size, last_maker, last_taker_size, last_taker)
                    # print(maker_size, maker, taker_size, taker)
                    if last_maker_size == maker_size:
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

        if last_sell_trade:
            if symbol not in inprogress_pingpong:
                inprogress_pingpong[symbol] = []
            inprogress_pingpong[symbol].append(last_sell_trade)

    return completed_pingpong, inprogress_pingpong, profit_pingpong


def custom_sort(item):
    return (item[0], item[1])


def display_results(completed_pingpong, inprogress_pingpong, profit_pingpong):
    completed_table_data = []

    for symbol, trades_list in completed_pingpong.items():
        for pingpongsequence in trades_list:
            for event in pingpongsequence:
                if isinstance(event, list):
                    timestamp, side, maker_size, maker, r_side, taker_size, taker = event
                    if side == "BUY":
                        date_str1 = pingpongsequence[0][0]
                        date_str2 = pingpongsequence[1][0]
                        date_format = "%Y-%m-%d %H:%M:%S,%f"
                        date1 = datetime.strptime(date_str1, date_format)
                        date2 = datetime.strptime(date_str2, date_format)
                        delta_date = str(date2 - date1).split('.')[0]
                        profit = pingpongsequence[2]
                    else:
                        delta_date = ""
                        profit = ""

                    completed_table_data.append(
                        (symbol, timestamp, side, maker_size, maker, r_side, taker_size, taker, profit, delta_date))

    headers = ["Symbol", "Timestamp", "Side", "Size T1", "Token1", "R_Side", "Size T2", "Token2", "Profit",
               "Exec time (D, h:m:s)"]

    print(tabulate(completed_table_data, headers=headers, tablefmt="pretty"))

    print("\nInprogress Pingpong:")
    inprogress_table_data = [(symbol, *trade) for symbol, trades in inprogress_pingpong.items() for trade in trades]
    sorted_inprogress = sorted(inprogress_table_data, key=custom_sort)
    print(tabulate(sorted_inprogress,
                   headers=["Symbol", "Timestamp", "Side", "Size T1", "Token1", "R_Side", "Size T2",
                            "Token2"],
                   tablefmt="pretty"))

    print("\nProfit Pingpong:")
    profit_table_data = [(symbol, profit['profit'], profit['asset']) for symbol, profit in profit_pingpong.items()]
    print(tabulate(profit_table_data, headers=["Symbol", "Profit", "Asset"], tablefmt="pretty"))


def parse_timeframe(timeframe_str):
    if timeframe_str.lower() == 'all':
        print("Timeframe: All")
        return None

    time_units = {
        'day': timedelta(days=1),
        'days': timedelta(days=1),
        'month': timedelta(days=30),
        'months': timedelta(days=30),
        'year': timedelta(days=365),
        'years': timedelta(days=365)
    }

    for unit, value in time_units.items():
        if unit in timeframe_str:
            num_units = int(timeframe_str.split(unit)[0])
            parsed_result = value * num_units
            print(f"Timeframe: {num_units} {unit}(s)")
            return parsed_result

    print("Invalid timeframe format. Supported formats: day(s), month(s), year(s)")
    return None


def main():
    parser = argparse.ArgumentParser(description='Pingpong Log Parser')
    parser.add_argument('--timeframe', type=str, default='all', help='Specify the timeframe to parse from now date (e.g., "3months", "4days", "2years")')
    args = parser.parse_args()

    log_file_path = "logs/pingpong_trade.log"
    trades = read_log_file(log_file_path, parse_timeframe(args.timeframe))
    completed_pingpong, inprogress_pingpong, profit_pingpong = calculate_profit(trades)
    display_results(completed_pingpong, inprogress_pingpong, profit_pingpong)


if __name__ == "__main__":
    main()
