import ast
import os
import sys

from dateutil import parser


def get_script_path():
    return os.path.dirname(os.path.realpath(sys.argv[0]))


infile = get_script_path() + "/logs/pingpong_trade.log"

keep_phrases = ["FINISHED"]
with open(infile) as f:
    f = f.readlines()

# EXTRACT FINISHED ORDERS DICT
orders = []
for count, line in enumerate(f):
    for phrase in keep_phrases:
        if phrase in line:
            sep1 = line.find("FINISHED")
            sep1 = line.find(":", sep1) + 2
            sep2 = line.find("\n", sep1)
            date = f[count + 1][0:25]
            str_a = f[count + 1][33::]
            dict_a = ast.literal_eval(str_a)
            orders.append([date, dict_a])
            break

# EXTRACT PINGPONG SEQUENCES
completed_sell = []
completed_buy = []
inprogress = []
for count1, order in enumerate(orders):
    complete = False
    if order[1]['side'] == 'SELL':
        for count2, order2 in enumerate(orders):
            if count2 > count1:
                if order2[1]['side'] == 'BUY' and order2[1]['taker_size'] == order[1]['maker_size']:
                    completed_sell.append(order)
                    completed_buy.append(order2)
                    complete = True
                    break
        if not complete:
            inprogress.append(order)

# CALC PROFIT / PRINT TO CONSOLE
print('inprogress orders:')
for i, order in enumerate(inprogress):
    inprogress_msg = f"{inprogress[i][0]:<26}{inprogress[i][1]['side']:<4} {'{:.6f}'.format(inprogress[i][1]['maker_size']):<8} {inprogress[i][1]['maker']:<3} {'TO':<4} {'{:.6f}'.format(inprogress[i][1]['taker_size']):<9} {inprogress[i][1]['taker']:<10}"
    print(inprogress_msg)

profit_dic = {}
print()
print('completed orders:')
for i, order in enumerate(completed_sell):
    sell_msg = f"{completed_sell[i][0]:<26}{completed_sell[i][1]['side']:<4} {'{:.6f}'.format(completed_sell[i][1]['maker_size']):<8} {completed_sell[i][1]['maker']:<3} {'TO':<4} {'{:.6f}'.format(completed_sell[i][1]['taker_size']):<9} {completed_sell[i][1]['taker']:<10}"
    buy_msg = f"{completed_buy[i][0]:<26}{completed_buy[i][1]['side']:<4} {'{:.6f}'.format(completed_buy[i][1]['taker_size']):<8} {completed_buy[i][1]['taker']:<3} {'WITH':<4} {'{:.6f}'.format(completed_buy[i][1]['maker_size']):<9} {completed_buy[i][1]['maker']:<10}"
    print(sell_msg)
    print(buy_msg)
    profit = completed_sell[i][1]['taker_size'] - completed_buy[i][1]['maker_size']
    date1 = parser.parse(completed_sell[i][0][1:-1])
    date2 = parser.parse(completed_buy[i][0][1:-1])
    delay = (date2 - date1)
    # print(type(delay))
    # exit()
    # exit()
    # delay_d = "{:.2f}".format(delay_s / 86400)

    print('PROFIT:', "{:.6f}".format(profit), completed_sell[i][1]['taker'], 'time_to_execute(D, h:m:s):', str(delay),
          '\n')
    if completed_sell[i][1]['taker'] in profit_dic:
        profit_dic[completed_sell[i][1]['taker']] += profit
    else:
        profit_dic[completed_sell[i][1]['taker']] = profit

print('Profit summary:')
print(profit_dic)
