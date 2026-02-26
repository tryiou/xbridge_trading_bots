"""Backtest data recorder - tracks all bot actions for analysis."""

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BalanceSnapshot:
    candle_idx: int
    price_close: float
    balance_a: float
    balance_b: float
    total_value_a: float
    total_value_b: float
    ratio_a: float
    skew: float
    concentration_triggered: bool


@dataclass
class TradeRecord:
    candle_idx: int
    side: str
    amount: float
    price: float
    quote_delta: float
    base_delta: float
    balance_a_after: float
    balance_b_after: float


@dataclass
class OrderBookSnapshot:
    candle_idx: int
    side: str
    level: int
    price: float
    amount: float
    status: str


@dataclass
class EventRecord:
    candle_idx: int
    event_type: str
    skew: float
    ratio_a: float
    details: str


@dataclass
class ActionRecord:
    candle_idx: int
    action_type: str
    side: Optional[str]
    level: Optional[int]
    price: Optional[float]
    amount: Optional[float]
    skew_before: float
    ratio_a: float
    concentration: bool
    details: str


class BacktestRecorder:
    """Records all backtest data to CSV files for analysis."""

    def __init__(self, output_dir: str, sample_interval: int = 10):
        self.output_dir = output_dir
        self.sample_interval = sample_interval

        os.makedirs(output_dir, exist_ok=True)

        self.balances: list[BalanceSnapshot] = []
        self.trades: list[TradeRecord] = []
        self.orderbooks: list[OrderBookSnapshot] = []
        self.events: list[EventRecord] = []
        self.actions: list[ActionRecord] = []

        self._prev_concentration = False
        self._order_id_to_tracking: dict = {}

    def record_balance(
        self,
        candle_idx: int,
        price_close: float,
        balance_a: float,
        balance_b: float,
        total_value_base: float,
        total_value_quote: float,
        ratio_a: float,
        skew: float,
        concentration_triggered: bool,
    ):
        self.balances.append(
            BalanceSnapshot(
                candle_idx=candle_idx,
                price_close=price_close,
                balance_a=balance_a,
                balance_b=balance_b,
                total_value_a=total_value_base,
                total_value_b=total_value_quote,
                ratio_a=ratio_a,
                skew=skew,
                concentration_triggered=concentration_triggered,
            )
        )

    def record_trade(
        self,
        candle_idx: int,
        side: str,
        amount: float,
        price: float,
        quote_delta: float,
        base_delta: float,
        balance_a_after: float,
        balance_b_after: float,
    ):
        self.trades.append(
            TradeRecord(
                candle_idx=candle_idx,
                side=side,
                amount=amount,
                price=price,
                quote_delta=quote_delta,
                base_delta=base_delta,
                balance_a_after=balance_a_after,
                balance_b_after=balance_b_after,
            )
        )

    def record_order_snapshot(
        self,
        candle_idx: int,
        side: str,
        level: int,
        price: float,
        amount: float,
        status: str,
    ):
        self.orderbooks.append(
            OrderBookSnapshot(
                candle_idx=candle_idx,
                side=side,
                level=level,
                price=price,
                amount=amount,
                status=status,
            )
        )

    def record_event(
        self,
        candle_idx: int,
        event_type: str,
        skew: float,
        ratio_a: float,
        details: str,
    ):
        self.events.append(
            EventRecord(
                candle_idx=candle_idx,
                event_type=event_type,
                skew=skew,
                ratio_a=ratio_a,
                details=details,
            )
        )

    def record_action(
        self,
        candle_idx: int,
        action_type: str,
        side: Optional[str],
        level: Optional[int],
        price: Optional[float],
        amount: Optional[float],
        skew_before: float,
        ratio_a: float,
        concentration: bool,
        details: str,
    ):
        self.actions.append(
            ActionRecord(
                candle_idx=candle_idx,
                action_type=action_type,
                side=side,
                level=level,
                price=price,
                amount=amount,
                skew_before=skew_before,
                ratio_a=ratio_a,
                concentration=concentration,
                details=details,
            )
        )

    def check_concentration_event(self, candle_idx: int, ratio_a: float, skew: float):
        concentration = ratio_a > 0.75 or ratio_a < 0.25
        if concentration and not self._prev_concentration:
            self.record_event(
                candle_idx,
                "concentration_exceeded",
                skew,
                ratio_a,
                f"ratio_a={ratio_a:.2f}",
            )
        elif not concentration and self._prev_concentration:
            self.record_event(
                candle_idx,
                "concentration_normal",
                skew,
                ratio_a,
                "back to normal",
            )
        self._prev_concentration = concentration

    def save_all(self, config: dict, summary: dict):
        self._save_balances()
        self._save_trades()
        self._save_orderbook()
        self._save_events()
        self._save_actions()
        self._save_summary(config, summary)

    def _save_balances(self):
        path = os.path.join(self.output_dir, "balances.csv")
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "candle_idx",
                    "price_close",
                    "balance_a",
                    "balance_b",
                    "total_value_base",
                    "total_value_quote",
                    "ratio_a",
                    "skew",
                    "concentration_triggered",
                ]
            )
            for b in self.balances:
                writer.writerow(
                    [
                        b.candle_idx,
                        f"{b.price_close:.4f}",
                        f"{b.balance_a:.4f}",
                        f"{b.balance_b:.4f}",
                        f"{b.total_value_a:.4f}",
                        f"{b.total_value_b:.4f}",
                        f"{b.ratio_a:.4f}",
                        f"{b.skew:.4f}",
                        str(b.concentration_triggered),
                    ]
                )

    def _save_trades(self):
        path = os.path.join(self.output_dir, "trades.csv")
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "candle_idx",
                    "side",
                    "amount",
                    "price",
                    "quote_delta",
                    "base_delta",
                    "balance_a_after",
                    "balance_b_after",
                ]
            )
            for t in self.trades:
                writer.writerow(
                    [
                        t.candle_idx,
                        t.side,
                        f"{t.amount:.6f}",
                        f"{t.price:.4f}",
                        f"{t.quote_delta:.4f}",
                        f"{t.base_delta:.6f}",
                        f"{t.balance_a_after:.4f}",
                        f"{t.balance_b_after:.4f}",
                    ]
                )

    def _save_orderbook(self):
        path = os.path.join(self.output_dir, "orderbook.csv")
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["candle_idx", "side", "level", "price", "amount", "status"]
            )
            for o in self.orderbooks:
                writer.writerow(
                    [
                        o.candle_idx,
                        o.side,
                        o.level,
                        f"{o.price:.4f}",
                        f"{o.amount:.6f}",
                        o.status,
                    ]
                )

    def _save_events(self):
        path = os.path.join(self.output_dir, "events.csv")
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["candle_idx", "event_type", "skew", "ratio_a", "details"])
            for e in self.events:
                writer.writerow(
                    [
                        e.candle_idx,
                        e.event_type,
                        f"{e.skew:.4f}",
                        f"{e.ratio_a:.4f}",
                        e.details,
                    ]
                )

    def _save_actions(self):
        path = os.path.join(self.output_dir, "actions.csv")
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "candle_idx",
                    "action_type",
                    "side",
                    "level",
                    "price",
                    "amount",
                    "skew_before",
                    "ratio_a",
                    "concentration",
                    "details",
                ]
            )
            for a in self.actions:
                writer.writerow(
                    [
                        a.candle_idx,
                        a.action_type,
                        a.side or "",
                        a.level or "",
                        f"{a.price:.4f}" if a.price else "",
                        f"{a.amount:.6f}" if a.amount else "",
                        f"{a.skew_before:.4f}",
                        f"{a.ratio_a:.4f}",
                        str(a.concentration),
                        a.details,
                    ]
                )

    def _save_summary(self, config: dict, summary: dict):
        path = os.path.join(self.output_dir, "summary.json")

        summary_clean = {
            k: v for k, v in summary.items() if k not in ["trades", "fake_balances"]
        }
        summary_clean["num_trades"] = len(self.trades)

        data = {
            "config": config,
            "summary": summary_clean,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
