# cancel all own open orders on xbridge

import asyncio
import os

from definitions.logger import setup_logger
from definitions.xbridge_def import XBridgeManager

if __name__ == '__main__':
    """
    A simple script to cancel all open XBridge orders.
    It initializes the necessary components to communicate with the Blocknet daemon.
    """


    class MinimalConfig:
        def __init__(self, logger):
            self.general_log = logger


    # Setup a basic logger for this script
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    general_log, _, _ = setup_logger(strategy="cancel_script", ROOT_DIR=ROOT_DIR)

    general_log.info("Initializing to cancel all orders...")
    xbridge_manager = XBridgeManager(MinimalConfig(general_log))
    general_log.info("Sending cancel all orders command...")
    asyncio.run(xbridge_manager.cancelallorders())
    general_log.info("All open orders have been cancelled.")
