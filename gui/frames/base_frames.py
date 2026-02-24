# gui/frames/base_frames.py
import abc
import asyncio
import logging
import threading
from tkinter import ttk
from typing import TYPE_CHECKING, Any

from definitions.config_manager import ConfigManager
from gui.components.data_panels import OrdersPanel
from gui.utils.async_helpers import run_async_in_bg
from gui.utils.async_updater import AsyncUpdater

if TYPE_CHECKING:
    from gui.config_windows.base_config_window import BaseConfigWindow
    from gui.main_app import MainApplication

logger = logging.getLogger(__name__)


class BaseStrategyFrame(ttk.Frame):
    """Base class for strategy-specific frames in the GUI with centralized error handling."""

    def __init__(
            self,
            parent,
            main_app: "MainApplication",
            strategy_name: str,
            master_config_manager: ConfigManager,
    ):
        super().__init__(parent)
        self.main_app = main_app
        self.master_config_manager = master_config_manager
        self.strategy_name = strategy_name
        self.config_manager: ConfigManager | None = None
        self.cancel_all_thread: threading.Thread | None = None
        self.started = False
        self.stopping = False
        self.cleaned = True
        self.btn_start: ttk.Button | None = None
        self.btn_stop: ttk.Button | None = None
        self.btn_cancel_all: ttk.Button | None = None
        self.btn_configure: ttk.Button | None = None
        self.orders_updater: AsyncUpdater | None = None
        self.orders_panel: OrdersPanel | None = None

        self.initialize_config()
        self.create_widgets()

    def initialize_config(self, loadxbridgeconf: bool = True):
        try:
            self.config_manager = ConfigManager(
                strategy=self.strategy_name, master_manager=self.master_config_manager
            )
            self.config_manager.initialize(loadxbridgeconf=loadxbridgeconf)
            if self.config_manager.strategy_instance:
                self.config_manager.strategy_instance.register_critical_error_callback(
                    lambda e: self.main_app.root.after(
                        0, self._critical_error_handler, e
                    )
                )
        except Exception as e:
            error_msg = f"Error initializing {self.strategy_name}: {e}"
            self.main_app.status_var.set(error_msg)
            if self.config_manager:
                self.config_manager.error_handler.handle(
                    e, context={"strategy": self.strategy_name}
                )

    def create_widgets(self):
        pass

    def _pre_start_validation(self):
        if not self.config_manager:
            raise RuntimeError("Config manager not initialized")
        if not hasattr(self.config_manager, "strategy_instance"):
            raise RuntimeError("Strategy not properly initialized")
        if (
                self.config_manager.strategy_instance
                and self.config_manager.strategy_instance.is_running
        ):
            raise RuntimeError("Bot thread already running")

    def _critical_error_handler(self, error):
        self.stop()
        error_msg = f"CRITICAL ERROR: {error!s}"
        self.main_app.status_var.set(error_msg)
        if self.config_manager:
            self.config_manager.error_handler.handle(
                error, context={"strategy": self.strategy_name}
            )

    def start(self):
        try:
            self._pre_start_validation()
            log = self.config_manager.general_log
            log.info("User clicked START for %s", self.strategy_name)
            self.stopping = False
            self.main_app.status_var.set(
                f"{self.strategy_name.capitalize()} bot is running..."
            )

            self.config_manager.strategy_instance.start()

            self.started = True
            self.cleaned = False
            self.update_button_states()
            self.start_refresh()
            self.main_app.notify_strategy_started(self.strategy_name)
        except Exception as e:
            error_msg = f"Error starting {self.strategy_name} bot: {e}"
            self.main_app.status_var.set(error_msg)
            if self.config_manager:
                self.config_manager.error_handler.handle(
                    e, context={"strategy": self.strategy_name}
                )
            else:
                logger.error(error_msg, exc_info=True)
            self.stop(reload_config=False)

    def stop(self, reload_config: bool = True) -> threading.Thread | None:
        if not self.config_manager or not self.started or self.stopping:
            return None

        self.stopping = True
        self.update_button_states()
        self.main_app.status_var.set(f"Stopping {self.strategy_name} bot...")

        def stopper_thread_func():
            if self.config_manager and self.config_manager.strategy_instance:
                self.config_manager.strategy_instance.stop()
            if self.winfo_exists():
                self.after(0, self._finalize_stop, reload_config)

        stopper_thread = threading.Thread(
            target=stopper_thread_func,
            daemon=True,
            name=f"StopperThread-{self.strategy_name}",
        )
        stopper_thread.start()
        return stopper_thread

    def _finalize_stop(self, reload_config: bool = True):
        if self.config_manager:
            self.config_manager.general_log.debug(
                "GUI: Finalizing stop for %s. Reload config: %s",
                self.strategy_name,
                reload_config,
            )

        if (
                self.config_manager
                and self.config_manager.strategy_instance
                and self.config_manager.strategy_instance.is_running
        ):
            self.config_manager.general_log.warning(
                "Bot thread did not terminate gracefully."
            )
            self.main_app.status_var.set("Bot stopped (forcefully).")
        else:
            self.main_app.status_var.set("Bot stopped.")
            if self.config_manager:
                self.config_manager.general_log.info("Bot stopped successfully.")

        self.started = False
        self.stopping = False
        self.cleaned = True
        self.update_button_states()
        self.purge_and_recreate_widgets()

        if reload_config:
            self.reload_configuration(loadxbridgeconf=False)

        self.main_app.notify_strategy_stopped(self.strategy_name)

    def cancel_all(self):
        if not self.config_manager:
            return

        log = self.config_manager.general_log
        log.debug("GUI: cancel_all called")
        self.main_app.status_var.set("Cancelling all open orders...")

        async def worker_task():
            try:
                await self.config_manager.xbridge_manager.cancelallorders(
                    use_shutdown_event=False
                )
                self.main_app.root.after(
                    0,
                    lambda: self.main_app.status_var.set("Cancelled all open orders."),
                )
            except asyncio.CancelledError:
                log.debug("cancel_all worker cancelled")
            except Exception as exc:
                error_msg = f"Error cancelling orders: {exc}"
                self.main_app.root.after(
                    0, lambda msg=error_msg: self.main_app.status_var.set(msg)
                )
                self.config_manager.error_handler.handle(
                    exc, context={"stage": "cancel_all"}
                )
            finally:
                if self.orders_updater:
                    self.orders_updater.start()

        self.cancel_all_thread = run_async_in_bg(worker_task)

    def cancel_own_orders(self):
        if not self.config_manager or not self.config_manager.strategy_instance:
            return
        if hasattr(self.config_manager.strategy_instance, "cancel_own_orders"):
            run_async_in_bg(self.config_manager.strategy_instance.cancel_own_orders)

    def start_refresh(self):
        if self.orders_updater:
            self.orders_updater.start()

    @staticmethod
    def _get_flag(status: str) -> str:
        return (
            "V"
            if status
               in {
                   "open",
                   "new",
                   "created",
                   "accepting",
                   "hold",
                   "initialized",
                   "committed",
                   "finished",
               }
            else "X"
        )

    def _fetch_orders_data(self) -> list[dict[str, Any]]:
        orders = []
        if not self.config_manager or not hasattr(self.config_manager, "pairs"):
            return orders

        with self.config_manager.resource_lock:
            for pair_obj in self.config_manager.pairs.values():
                name = pair_obj.name
                symbol = pair_obj.symbol
                status = "None"
                current_order_side = "None"
                maker_size = "None"
                maker = "None"
                taker_size = "None"
                taker = "None"
                dex_price = "None"
                order_id = "None"

                if (
                        self.started
                        and pair_obj.dex.order
                        and "status" in pair_obj.dex.order
                ):
                    status = pair_obj.dex.order.get("status", "None")
                    current_order_side = (
                        pair_obj.dex.current_order.get("side", "None")
                        if pair_obj.dex.current_order
                        else "None"
                    )
                    maker_size = (
                        pair_obj.dex.current_order.get("maker_size", "None")
                        if pair_obj.dex.current_order
                        else "None"
                    )
                    maker = (
                        pair_obj.dex.current_order.get("maker", "None")
                        if pair_obj.dex.current_order
                        else "None"
                    )
                    taker_size = (
                        pair_obj.dex.current_order.get("taker_size", "None")
                        if pair_obj.dex.current_order
                        else "None"
                    )
                    taker = (
                        pair_obj.dex.current_order.get("taker", "None")
                        if pair_obj.dex.current_order
                        else "None"
                    )
                    dex_price = (
                        pair_obj.dex.current_order.get("dex_price", "None")
                        if pair_obj.dex.current_order
                        else "None"
                    )
                    order_id = (
                        pair_obj.dex.order.get("id", "None")
                        if pair_obj.dex.current_order
                        else "None"
                    )
                elif pair_obj.dex.disabled:
                    status = "Disabled"

                variation_display = "None"
                if (
                        self.started
                        and pair_obj.dex.order
                        and "status" in pair_obj.dex.order
                ):
                    variation_display = str(pair_obj.dex.variation)

                orders.append(
                    {
                        "name": name,
                        "symbol": symbol,
                        "status": status,
                        "side": current_order_side,
                        "flag": self._get_flag(status),
                        "variation": variation_display,
                        "maker_size": maker_size,
                        "maker": maker,
                        "taker_size": taker_size,
                        "taker": taker,
                        "dex_price": dex_price,
                        "order_id": order_id,
                    }
                )
        return orders

    def on_closing(self):
        if self.config_manager:
            self.config_manager.general_log.info(
                "Closing %s strategy...", self.strategy_name
            )
        self.stop(reload_config=False)

    def reload_configuration(self, loadxbridgeconf: bool = True):
        if not self.config_manager:
            return
        self.config_manager.general_log.debug(
            "GUI: Reloading configuration for %s.", self.strategy_name
        )
        self.initialize_config(loadxbridgeconf=loadxbridgeconf)
        self.purge_and_recreate_widgets()

    def purge_and_recreate_widgets(self):
        pass

    def create_standard_buttons(self):
        button_frame = ttk.Frame(self)
        button_frame.grid(column=0, row=0, padx=5, pady=5, sticky="ew")
        btn_width = 12
        self.btn_start = ttk.Button(
            button_frame, text="START", command=self.start, width=btn_width
        )
        self.btn_start.grid(column=0, row=0, padx=5, pady=5)
        self.btn_stop = ttk.Button(
            button_frame, text="STOP", command=self.stop, width=btn_width
        )
        self.btn_stop.grid(column=1, row=0, padx=5, pady=5)
        self.btn_cancel_all = ttk.Button(
            button_frame, text="CANCEL ALL", command=self.cancel_all, width=btn_width
        )
        self.btn_cancel_all.grid(column=2, row=0, padx=5, pady=5)
        self.btn_configure = ttk.Button(
            button_frame,
            text="CONFIGURE",
            command=self.open_configure_window,
            width=btn_width,
        )
        self.btn_configure.grid(column=3, row=0, padx=5, pady=5)
        self.update_button_states()

    def update_button_states(self):
        start_enabled = not self.started and not self.stopping
        stop_enabled = self.started and not self.stopping
        configure_enabled = not self.started and not self.stopping

        if self.btn_start:
            self.btn_start.config(state="normal" if start_enabled else "disabled")
        if self.btn_stop:
            self.btn_stop.config(state="normal" if stop_enabled else "disabled")
        if self.btn_configure:
            self.btn_configure.config(
                state="normal" if configure_enabled else "disabled"
            )

    def cleanup(self):
        self.unbind_all("<Motion>")
        self.unbind_all("<Button>")

    def open_configure_window(self):
        pass


class StandardStrategyFrame(BaseStrategyFrame, metaclass=abc.ABCMeta):
    def __init__(
            self,
            parent,
            main_app: "MainApplication",
            strategy_name: str,
            master_config_manager: ConfigManager,
    ):
        self.orders_panel: OrdersPanel
        self.gui_config: BaseConfigWindow
        super().__init__(parent, main_app, strategy_name, master_config_manager)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

    @abc.abstractmethod
    def _create_config_gui(self) -> "BaseConfigWindow":
        pass

    def create_widgets(self):
        self.orders_frame = ttk.LabelFrame(self, text="Orders")
        self.orders_frame.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        self.orders_frame.grid_rowconfigure(0, weight=1)
        self.orders_frame.grid_columnconfigure(0, weight=1)

        self.orders_panel = OrdersPanel(self.orders_frame)
        self.orders_panel.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")

        self.orders_updater = AsyncUpdater(
            tk_widget=self,
            update_target_method=self.orders_panel.update_data,
            fetch_data_callable=self._fetch_orders_data,
            update_interval_ms=1500,
            name=f"{self.strategy_name}OrdersUpdater",
        )

        self.gui_config = self._create_config_gui()
        self.create_standard_buttons()

    def open_configure_window(self):
        self.gui_config.open()

    def purge_and_recreate_widgets(self):
        self.orders_panel.destroy()
        self.orders_panel = OrdersPanel(self.orders_frame)
        self.orders_panel.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")
        self.orders_updater = AsyncUpdater(
            tk_widget=self,
            update_target_method=self.orders_panel.update_data,
            fetch_data_callable=self._fetch_orders_data,
            update_interval_ms=1500,
            name=f"{self.strategy_name}OrdersUpdater",
        )
        initial_orders_data = self._fetch_orders_data()
        self.orders_panel.update_data(initial_orders_data)

    def cleanup(self):
        pass
