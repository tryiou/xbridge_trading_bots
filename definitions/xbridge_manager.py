import asyncio
import configparser
import os
import uuid
import socket
import sys
import os
import subprocess
import time
from pathlib import Path

from definitions.detect_rpc import detect_rpc
from definitions.rpc import rpc_call


class XBridgeManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager

        self.logger = self.config_manager.general_log
        self.blocknet_user_rpc, self.blocknet_port_rpc, self.blocknet_password_rpc, self.blocknet_datadir_path = detect_rpc()
        self.xbridge_conf = None
        self.xbridge_fees_estimate = {}

        # Optional: Test RPC connection during initialization
        if not self.test_rpc():
            self.logger.error(f'Blocknet core rpc server not responding or credentials incorrect.')
            # Depending on desired behavior, you might want to raise an exception or exit here.
            # For now, just log the error.

        if getattr(self.config_manager, 'strategy', None) == "arbitrage":
            # Load and parse the xbridge.conf file
            self.parse_xbridge_conf()
            # Calculate fee estimates
            self.calculate_xbridge_fees()

    def isportopen(self, ip, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect((ip, int(port)))
            s.shutdown(2)
            return True
        except:
            return False

    async def rpc_wrapper(self, method, params=None):
        if params is None:
            params = []
        result = await rpc_call(method=method,
                                params=params,
                                rpc_user=self.blocknet_user_rpc,
                                rpc_port=self.blocknet_port_rpc,
                                rpc_password=self.blocknet_password_rpc,
                                debug=self.config_manager.config_xbridge.debug_level,
                                logger=self.logger)  # Pass the instance logger
        return result

    def test_rpc(self):
        # This method is called from constructor, so it cannot be async.
        # We will use asyncio.run to run the async rpc_wrapper.
        # This is acceptable for a one-off test at startup.
        result = asyncio.run(self.rpc_wrapper("getwalletinfo"))
        if result:
            self.logger.info(f'XBridge RPC connection successful: getwalletinfo returned {result}')
            return True
        else:
            self.logger.error(f'XBridge RPC connection failed: getwalletinfo returned {result}')
            return False

    def parse_xbridge_conf(self):
        """Parse the xbridge.conf file and store the configuration in self.xbridge_conf."""
        if not self.blocknet_datadir_path:
            self.logger.error("No Blocknet datadir path found, cannot parse xbridge.conf")
            return

        conf_path = os.path.join(self.blocknet_datadir_path, 'xbridge.conf')
        if not os.path.exists(conf_path):
            self.logger.error(f"xbridge.conf not found at {conf_path}")
            return

        config = configparser.ConfigParser()
        try:
            config.read(conf_path)
            self.xbridge_conf = {}

            # Get all supported coins (sections after [Main])                                                                                                                                   
            for section in config.sections():
                if section == 'Main':
                    continue

                # Skip if the section is not a coin (unlikely, but just in case)
                if not section.isupper() or not section.isalpha():
                    continue

                coin = section
                self.xbridge_conf[coin] = {}

                # Get all key-value pairs in the section                                                                                                                                        
                for key, value in config.items(section):
                    # Convert numeric values to appropriate types    
                    # self.logger.info(f"key: {key}, value: {value}")
                    if key in ['coin', 'minimumamount', 'dustamount', 'txversion', 'blocktime', 'feeperbyte',
                               'mintxfee', 'confirmations', 'addressprefix', 'scriptprefix', 'secretprefix']:
                        self.xbridge_conf[coin][key] = int(value)
                    elif key in ['getnewkeysupported', 'importwithnoscansupported', 'lockcoinssupported',
                                 'txwithtimefield']:
                        self.xbridge_conf[coin][key] = bool(value)
                    else:
                        self.xbridge_conf[coin][key] = value

            self.logger.info(f"Successfully parsed xbridge.conf with {len(self.xbridge_conf)} coins")
            # self.logger.info(f"Parsed xbridge.conf {self.xbridge_conf}")
        except Exception as e:
            self.logger.error(f"Error parsing xbridge.conf: {str(e)}")
            self.xbridge_conf = None

    def calculate_xbridge_fees(self):
        """Calculate and store estimated XBridge transaction fees for each coin."""
        if not self.xbridge_conf:
            self.logger.error("Cannot calculate fees: xbridge.conf not loaded")
            return

        self.xbridge_fees_estimate = {}

        for coin, config in self.xbridge_conf.items():
            try:
                # Get the fee per byte and minimum transaction fee
                fee_per_byte = config.get('feeperbyte', 0)
                min_tx_fee = config.get('mintxfee', 0)

                # Estimate the fee for a typical transaction
                # We'll assume a typical transaction size of 500 bytes (this might need adjustment)
                typical_tx_size = 500
                estimated_fee = fee_per_byte * typical_tx_size

                # Ensure the fee doesn't go below the minimum
                estimated_fee = max(estimated_fee, min_tx_fee)

                # Convert to absolute value (in satoshis)
                estimated_fee_satoshis = estimated_fee

                # Convert to coin units (BTC, LTC, etc.)
                coin_units = config.get('coin', 100000000)
                estimated_fee_coin = estimated_fee_satoshis / coin_units

                self.xbridge_fees_estimate[coin] = {
                    'fee_per_byte': fee_per_byte,
                    'min_tx_fee': min_tx_fee,
                    'estimated_fee_satoshis': estimated_fee_satoshis,
                    'estimated_fee_coin': estimated_fee_coin,
                    'typical_tx_size': typical_tx_size
                }
            except Exception as e:
                self.logger.error(f"Error calculating fee estimate for {coin}: {str(e)}")
                self.xbridge_fees_estimate[coin] = None

        self.logger.info(f"XBridge fee estimates calculated for {len(self.xbridge_fees_estimate)} coins")
        # self.logger.info(f"XBridge fee estimates: {self.xbridge_fees_estimate}")

    async def getnewtokenadress(self, token):
        return await self.rpc_wrapper("dxGetNewTokenAddress", [token])

    async def getmyordersbymarket(self, maker, taker):
        myorders = await self.rpc_wrapper("dxGetMyOrders")
        return [zz for zz in myorders if (zz['maker'] == maker) and (zz['taker'] == taker)]

    async def cancelorder(self, order_id):
        return await self.rpc_wrapper("dxCancelOrder", [order_id])

    async def cancelallorders(self):
        myorders = await self.rpc_wrapper("dxGetMyOrders")
        for z in myorders:
            if z['status'] == "open" or z['status'] == "new":
                await self.cancelorder(z['id'])

    async def dxloadxbridgeconf(self):
        await self.rpc_wrapper("dxloadxbridgeconf")

    async def dxflushcancelledorders(self):
        return await self.rpc_wrapper("dxflushcancelledorders")

    async def gettokenbalances(self):
        return await self.rpc_wrapper("dxgettokenbalances")

    async def gettokenutxo(self, token, used=False):
        return await self.rpc_wrapper("dxgetutxos", [token, used])

    async def getlocaltokens(self):
        return await self.rpc_wrapper("dxgetlocaltokens")

    async def makeorder(self, maker, makeramount, makeraddress, taker, takeramount, takeraddress, dryrun=None):
        if dryrun:
            result = await self.rpc_wrapper("dxMakeOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             'exact', 'dryrun'])
        else:
            result = await self.rpc_wrapper("dxMakeOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             'exact'])
        return result

    async def makepartialorder(self, maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size,
                               repost=False, dryrun=None):
        if dryrun:
            result = await self.rpc_wrapper("dxMakePartialOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             min_size, repost, 'dryrun'])
        else:
            result = await self.rpc_wrapper("dxMakePartialOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             min_size, repost])
        return result

    async def getorderstatus(self, oid):
        return await self.rpc_wrapper("dxGetOrder", [oid])

    async def dxgetorderbook(self, detail, maker, taker):
        return await self.rpc_wrapper("dxgetorderbook", [detail, maker, taker])

    async def take_order(self, order_id: str, from_address: str, to_address: str, test_mode: bool = False):
        """Takes an XBridge order using dxTakeOrder."""
        self.logger.info(f"Attempting to take XBridge order {order_id} from {from_address} to {to_address}")

        if test_mode:
            mock_result = {'id': f'mock_xbridge_txid_{uuid.uuid4()}', 'status': 'created'}
            self.logger.info(f"[TEST MODE] Would execute BLOCKNET WALLET RPC CALL:")
            self.logger.info(f"    - RPC Port: {self.blocknet_port_rpc}")
            self.logger.info(f"    - Method: dxTakeOrder")
            self.logger.info(f"    - Params: ['{order_id}', '{from_address}', '{to_address}']")
            self.logger.info(f"    - Returning mock result: {mock_result}")
            return mock_result

        try:
            result = await self.rpc_wrapper("dxTakeOrder", [order_id, from_address, to_address])
            self.logger.info(f"Successfully took XBridge order {order_id}. Result: {result}")
            return result
        except Exception as e:
            self.logger.error(f"Failed to take XBridge order {order_id}. Error: {e}", exc_info=True)
            return None
