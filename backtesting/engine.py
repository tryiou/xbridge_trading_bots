"""Backtesting engine - uses fake RPC server to run real autonomous strategy."""

import asyncio
import logging
import os
import shutil
import tempfile
import warnings
from dataclasses import dataclass

import pandas as pd

from backtesting.backtest_recorder import BacktestRecorder
from backtesting.fake_xbridge_rpc import FakeXBridgeRPCServer
from backtesting.price_aggregator import PriceAggregator
from backtesting.price_feed import PriceFeed
from strategies.autonomous_maker_strategy import AutonomousMakerStrategy

logger = logging.getLogger(__name__)

logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("c-ares").setLevel(logging.ERROR)

warnings.filterwarnings("ignore", message=".*DNS resolver.*inotify.*")


# Default port for fake RPC server
FAKE_RPC_HOST = "127.0.0.1"
FAKE_RPC_PORT = 18332


@dataclass
class BacktestResults:
    pair: str
    start_date: str
    end_date: str
    interval: str
    prices: pd.DataFrame
    results: dict


class BacktestEngine:
    """
    Backtesting engine that runs the real AutonomousMakerStrategy
    with a fake XBridge RPC server.

    The strategy runs completely unchanged - it connects to our fake
    RPC server thinking it's the real XBridge blockchain.
    """

    def __init__(
        self,
        pair: str,
        start_date: str,
        end_date: str,
        interval: str,
        pair_config: dict,
        rpc_host: str = FAKE_RPC_HOST,
        rpc_port: int = FAKE_RPC_PORT,
        output_dir: str | None = None,
        backtest_name: str = "",
    ):
        self.pair = pair
        self.start_date = start_date
        self.end_date = end_date
        self.interval = interval
        self.pair_config = pair_config
        self.rpc_host = rpc_host
        self.rpc_port = rpc_port

        self.base_token, self.quote_token = pair.split("/")

        if output_dir:
            self.output_dir = output_dir
        else:
            results_dir = os.path.join(
                os.path.dirname(__file__), "..", "backtest_results"
            )
            os.makedirs(results_dir, exist_ok=True)
            pair_name = pair_config.get("name", "pair")
            name = f"{pair_name}_{start_date}_{end_date}_{backtest_name}"
            self.output_dir = os.path.join(results_dir, name)
        os.makedirs(self.output_dir, exist_ok=True)

        self.recorder = BacktestRecorder(self.output_dir, sample_interval=10)

        # Create sandbox directory for backtest state files
        self.sandbox_dir = tempfile.mkdtemp(prefix="xbridge_backtest_")
        logger.info("Using sandbox directory: %s", self.sandbox_dir)

        self.price_feed = PriceFeed()
        self.price_aggregator = PriceAggregator(
            self.price_feed,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

        self.fake_rpc_server: FakeXBridgeRPCServer = None
        self.current_price: float = 0.0
        self.prices: pd.DataFrame = None

        self.strategy: AutonomousMakerStrategy = None
        self.config_manager = None

    def run(self) -> BacktestResults:
        """Run the backtest."""
        logger.info("Starting backtest for %s", self.pair)

        self.prices = self._fetch_price_data()

        # Start fake RPC server first
        self._start_fake_rpc_server()

        try:
            self._setup_and_run_strategy()
        finally:
            self._stop_fake_rpc_server()
            self._cleanup_sandbox()

        results = self._collect_results()

        logger.info(
            "Backtest complete: %d trades, final balance: %.4f %s / %.4f %s",
            results["total_trades"],
            results["final_balance_a"],
            self.base_token,
            results["final_balance_b"],
            self.quote_token,
        )

        return BacktestResults(
            pair=self.pair,
            start_date=self.start_date,
            end_date=self.end_date,
            interval=self.interval,
            prices=self.prices,
            results=results,
        )

    def _fetch_price_data(self) -> pd.DataFrame:
        """Fetch historical price data."""
        logger.info(
            "Fetching price data: %s from %s to %s (%s)",
            self.pair,
            self.start_date,
            self.end_date,
            self.interval,
        )

        prices = self.price_aggregator.get_synthetic_pair(
            base=self.base_token,
            quote=self.quote_token,
            start=self.start_date,
            end=self.end_date,
        )

        # Auto-detect mid_price from first price if not set in config
        if self.pair_config.get("initial_mid_price") is None:
            first_close = float(prices["Close"].iloc[0])
            self.pair_config["initial_mid_price"] = first_close
            logger.info(
                "Auto-detected mid_price from data: %.2f (first close)", first_close
            )
        else:
            logger.info(
                "Using configured mid_price: %.2f",
                self.pair_config["initial_mid_price"],
            )

        return prices

    def _start_fake_rpc_server(self):
        """Start the fake RPC server in a background thread."""
        logger.info(
            "Starting fake XBridge RPC server on %s:%d", self.rpc_host, self.rpc_port
        )

        self.fake_rpc_server = FakeXBridgeRPCServer(
            host=self.rpc_host,
            port=self.rpc_port,
        )
        self.fake_rpc_server.set_tokens(self.base_token, self.quote_token)
        self.fake_rpc_server.set_balances(
            self.pair_config.get("initial_balance_a", 0),
            self.pair_config.get("initial_balance_b", 0),
        )

        # Run server in background thread with persistent event loop
        import threading

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.fake_rpc_server.start())
            loop.run_forever()  # Keep loop running to serve requests

        self._rpc_server_thread = threading.Thread(target=run_server, daemon=True)
        self._rpc_server_thread.start()

        # Wait for server to be ready
        import time

        time.sleep(0.5)

        logger.info("Fake XBridge RPC server running")

    def _stop_fake_rpc_server(self):
        """Stop the fake RPC server."""
        if self.fake_rpc_server:
            logger.info("Stopping fake XBridge RPC server")
            asyncio.run(self.fake_rpc_server.stop())

    def _cleanup_sandbox(self):
        """Clean up sandbox directory after backtest."""
        if hasattr(self, "sandbox_dir") and self.sandbox_dir:
            try:
                shutil.rmtree(self.sandbox_dir)
                logger.info("Cleaned up sandbox directory: %s", self.sandbox_dir)
            except Exception as e:
                logger.warning("Failed to cleanup sandbox: %s", e)

    def _get_current_price(self) -> float:
        """Callback for fake RPC server to get current price."""
        return self.current_price

    def _setup_and_run_strategy(self):
        """Set up and run the strategy using real ConfigManager."""
        logger.debug("Setting up strategy with real ConfigManager")

        from definitions.config_manager import ConfigManager
        from definitions.main_controller import MainController
        from definitions.xbridge_manager import XBridgeManager

        # Inject fake RPC config BEFORE creating ConfigManager
        # This overrides the real blocknet.conf settings
        XBridgeManager._rpc_config = (
            "fakeuser",  # rpc_user
            self.rpc_port,  # rpc_port - our fake server port
            "fakepass",  # rpc_password
            "/tmp/fake",  # datadir_path
        )
        logger.info("Injected fake RPC config: port %d", self.rpc_port)

        # Create real ConfigManager - loads real configs, creates real XBridgeManager
        # which connects to our fake RPC server
        self.config_manager = ConfigManager(strategy="autonomous_maker")

        # Override ROOT_DIR to use sandbox directory
        self.config_manager.ROOT_DIR = self.sandbox_dir
        self.config_manager.config_loader.root_dir = self.sandbox_dir

        # Disable validation to skip template check
        self.config_manager.config_loader.validation_enabled = False

        # Override pair config BEFORE initialization so strategy uses our backtest values
        self.config_manager.config_autonomous_maker.pair_configs = [self.pair_config]

        # Initialize config - this creates strategy_instance with OUR pair_config
        self.config_manager.initialize()

        # Get strategy reference
        self.strategy = self.config_manager.strategy_instance

        # Initialize pair for backtest - must be called BEFORE simulation
        self.strategy._initialize_for_pair(self.pair_config)

        # DEBUG: Log the mid_price being used
        logger.info(
            "DEBUG: mid_price=%.6f, initial_mid_price=%.6f",
            self.strategy.mid_price,
            self.strategy.initial_mid_price,
        )
        logger.info(
            "DEBUG: pricing_engine.mid_price=%.6f",
            self.strategy.pricing_engine.mid_price,
        )

        # Create controller
        loop = asyncio.new_event_loop()
        self.controller = MainController(self.config_manager, loop)

        self._run_simulation()

    def _run_simulation(self):
        """Run the main simulation loop."""
        prices = self.prices

        price_min = prices["Close"].min()
        price_max = prices["Close"].max()
        price_first = prices["Close"].iloc[0]
        price_last = prices["Close"].iloc[-1]

        logger.info(
            "Price range: %.2f - %.2f (first: %.2f, last: %.2f)",
            price_min,
            price_max,
            price_first,
            price_last,
        )

        class MockPair:
            def __init__(self, cfg):
                self.cfg = cfg

        mock_pair = MockPair(self.pair_config)

        self.strategy._save_orders = lambda: None

        def get_total_value(balance_a, balance_b):
            total_base = balance_a + (balance_b / self.current_price)
            total_quote = (balance_a * self.current_price) + balance_b
            return total_base, total_quote

        self._prev_order_count = 0

        async def run_loop():
            logger.info("Starting simulation loop with %d price rows", len(prices))
            for i, (_idx, row) in enumerate(prices.iterrows()):
                self.current_price = float(row["Close"])
                candle_low = float(row["Low"])
                candle_high = float(row["High"])

                self._check_and_fill_orders(candle_low, candle_high, i)

                prev_order_ids = set(self.strategy.order_ladder.orders.keys())

                await self.strategy.process_pair_async(mock_pair)

                new_order_ids = set(self.strategy.order_ladder.orders.keys())
                newly_created_ids = new_order_ids - prev_order_ids

                balance_a = self.strategy.inventory_manager.current_balance_a
                balance_b = self.strategy.inventory_manager.current_balance_b

                ratio_a = self.strategy.inventory_manager.get_ratio_a()
                skew = self.strategy.inventory_manager.calculate_skew()
                concentration = ratio_a > 0.75 or ratio_a < 0.25

                for order_id, order in self.strategy.order_ladder.orders.items():
                    if order.status == "open" and order_id in newly_created_ids:
                        self.recorder.record_action(
                            candle_idx=i,
                            action_type="order_created",
                            side=order.side,
                            level=order.level,
                            price=order.price,
                            amount=order.amount,
                            skew_before=skew,
                            ratio_a=ratio_a,
                            concentration=concentration,
                            details=f"created {order.side} @ {order.price}",
                        )

                balance_a = self.strategy.inventory_manager.current_balance_a
                balance_b = self.strategy.inventory_manager.current_balance_b
                total_value_base, total_value_quote = get_total_value(
                    balance_a, balance_b
                )
                ratio_a = self.strategy.inventory_manager.get_ratio_a()
                skew = self.strategy.inventory_manager.calculate_skew()
                concentration = ratio_a > 0.75 or ratio_a < 0.25

                self.recorder.record_balance(
                    candle_idx=i,
                    price_close=self.current_price,
                    balance_a=balance_a,
                    balance_b=balance_b,
                    total_value_base=total_value_base,
                    total_value_quote=total_value_quote,
                    ratio_a=ratio_a,
                    skew=skew,
                    concentration_triggered=concentration,
                )

                self.recorder.check_concentration_event(i, ratio_a, skew)

                if len(self.strategy.trade_recorder.trades) > self._prev_order_count:
                    new_trades = self.strategy.trade_recorder.trades[
                        self._prev_order_count :
                    ]

                    for trade in new_trades:
                        if trade.side == "buy":
                            quote_delta = -trade.amount * trade.price
                            base_delta = trade.amount
                        else:
                            quote_delta = trade.amount * trade.price
                            base_delta = -trade.amount

                        self.recorder.record_trade(
                            candle_idx=i,
                            side=trade.side,
                            amount=trade.amount,
                            price=trade.price,
                            quote_delta=quote_delta,
                            base_delta=base_delta,
                            balance_a_after=trade.balance_a_after,
                            balance_b_after=trade.balance_b_after,
                        )
                        self.recorder.record_action(
                            candle_idx=i,
                            action_type="order_filled",
                            side=trade.side,
                            level=None,
                            price=trade.price,
                            amount=trade.amount,
                            skew_before=skew,
                            ratio_a=ratio_a,
                            concentration=concentration,
                            details=f"filled @ {trade.price}",
                        )
                    self._prev_order_count = len(self.strategy.trade_recorder.trades)

                if i % self.recorder.sample_interval == 0:
                    for order in self.strategy.order_ladder.get_buy_orders():
                        self.recorder.record_order_snapshot(
                            candle_idx=i,
                            side="buy",
                            level=order.level,
                            price=order.price,
                            amount=order.amount,
                            status=order.status,
                        )
                    for order in self.strategy.order_ladder.get_sell_orders():
                        self.recorder.record_order_snapshot(
                            candle_idx=i,
                            side="sell",
                            level=order.level,
                            price=order.price,
                            amount=order.amount,
                            status=order.status,
                        )

                if (i + 1) % 500 == 0:
                    logger.info("Processed %d/%d candles", i + 1, len(prices))
            logger.info("Finished simulation loop")

        asyncio.run(run_loop())

    def _check_and_fill_orders(
        self, candle_low: float, candle_high: float, candle_idx: int
    ):
        """Check all orders and execute trades if price crossed the order level."""
        if not self.fake_rpc_server:
            return

        orders = self.fake_rpc_server.orders

        for order_id, order in list(orders.items()):
            status = order.get("status", "unknown")
            if status != "open":
                continue

            maker = order["maker"]
            maker_size = float(order["maker_size"])
            taker_size = float(order["taker_size"])

            if maker == self.quote_token:
                order_price = maker_size / taker_size if taker_size > 0 else 0
            else:
                order_price = taker_size / maker_size if maker_size > 0 else 0

            if candle_low <= order_price <= candle_high:
                self.fake_rpc_server.execute_trade(order_id)

                logger.info(
                    "Order %s executed at price %.2f (candle: %.2f-%.2f)",
                    order_id[:8],
                    order_price,
                    candle_low,
                    candle_high,
                )

    def _collect_results(self) -> dict:
        """Collect results from strategy state."""
        inv = self.strategy.inventory_manager
        trades = self.strategy.trade_recorder.trades

        buy_trades = [t for t in trades if t.side == "buy"]
        sell_trades = [t for t in trades if t.side == "sell"]

        initial_a = inv.initial_balance_a
        initial_b = inv.initial_balance_b
        final_a = inv.current_balance_a
        final_b = inv.current_balance_b

        initial_mid_price = self.pair_config.get("initial_mid_price", 1)
        final_price = self.prices["Close"].iloc[-1]

        initial_value = initial_a + (initial_b / initial_mid_price)
        final_value = final_a + (final_b / final_price)

        fake_state = self.fake_rpc_server.get_state() if self.fake_rpc_server else {}

        results = {
            "initial_balance_a": initial_a,
            "initial_balance_b": initial_b,
            "final_balance_a": final_a,
            "final_balance_b": final_b,
            "profit_a": final_a - initial_a,
            "profit_b": final_b - initial_b,
            "total_trades": self.strategy.trade_count,
            "buy_trades": len(buy_trades),
            "sell_trades": len(sell_trades),
            "total_return_percent": (
                ((final_value - initial_value) / initial_value * 100)
                if initial_value > 0
                else 0
            ),
            "avg_trade_size": (
                sum(t.amount for t in trades) / len(trades) if trades else 0
            ),
            "trades": trades,
            "fake_balances": fake_state.get("balances", {}),
            "fake_orders": fake_state.get("order_count", 0),
            "output_dir": self.output_dir,
        }

        self.recorder.save_all(
            config={
                "pair": self.pair,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "interval": self.interval,
                "pair_config": self.pair_config,
            },
            summary=results,
        )

        return results
