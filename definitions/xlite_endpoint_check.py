import time

import requests

import definitions.xbridge_def as xb
# from definitions.classes import general_log


def xlite_endpoint_height_check(cc_coins, return_data=True, display=True):
    chainz_summary = get_chainz_summary()
    block_tolerance = 3
    disabled_coins = []
    result = []
    # print('ha')
    if len(cc_coins) > 0 and chainz_summary:
        for coin in cc_coins:
            valid = None
            chainz_height = None
            cc_height = get_cc_height(coin)
            if cc_height:
                if coin.casefold() in chainz_summary:
                    chainz_height = chainz_summary[coin.casefold()]['height']
                elif coin.casefold() == 'doge':
                    try:
                        chainz_height = int(xb.xrgetblockcount('DOGE', 2, max_err_count=3)['reply'])
                    except Exception as e:
                        print('xb.xrgetblockcount("DOGE", 2) fail\n', type(e), e)
                        chainz_height = None
                else:
                    chainz_height = None
                if chainz_height:
                    if chainz_height + block_tolerance >= cc_height >= chainz_height - block_tolerance:
                        valid = True
                    else:
                        valid = False
                else:
                    valid = False
            else:
                cc_height = None
            if cc_height is None or valid is False:
                disabled_coins.append(coin)
            result.append(
                {'coin': coin, 'cc_height': cc_height, 'chainz_height': chainz_height, 'valid': valid})
        if display:
            print('cc_height_check:')
            for line in result:
                print(line)
        # if len(disabled_coins) > 0:
        #     general_log.info(msg="cc_height_check, disabled_coins: " + str(disabled_coins))
        if return_data:
            return disabled_coins


def get_cc_height(coin):
    cc_blockcount = None
    got_cc_height = False
    error_count = 0
    maxi = 3
    while got_cc_height is False:
        result = None
        try:
            result = xb.rpc_call(method="getblockcount", params=[coin],
                                 url="https://plugin-api.core.cloudchainsinc.com", port=443, rpc_user=None,
                                 rpc_password=None)
            cc_blockcount = int(result)
        except Exception as e:
            error_count += 1
            print('check_cloudchains_blockcounts:', coin, type(e), str(e), 'error_count:', error_count,
                  'got_cc_height:', got_cc_height, '\n' + str(result))
            if error_count >= maxi:
                cc_blockcount = None
                print("cc_blockcount error:\n" + str(type(e)) + "\n" + str(e))
                got_cc_height = True
            else:
                time.sleep(error_count)
        else:
            got_cc_height = True
    return cc_blockcount


def get_chainz_summary():
    chainz_url = "https://chainz.cryptoid.info/explorer/api.dws?q=summary"
    counter = 0
    maxi = 3
    done = False
    while not done:
        counter += 1
        if counter >= maxi:
            return None
        try:
            chainz_summary = requests.get(chainz_url).json()
        except Exception as e:
            print("chainz_summary error:\n" + str(type(e)) + "\n" + str(e))
            time.sleep(0.5)
        else:
            return chainz_summary
