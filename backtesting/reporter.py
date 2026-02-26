"""Backtest results reporter."""

import csv
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List

from .engine import BacktestResults


@dataclass
class EquityPoint:
    """Represents a point in the equity curve."""

    timestamp: Any
    price: float
    balance_a: float
    balance_b: float
    total_equity_a: float


class Reporter:
    """Generates reports from backtest results."""

    def generate_report(self, results: BacktestResults) -> str:
        """Generate a human-readable backtest report."""
        r = results.results
        pair = results.pair
        start_date = results.start_date
        end_date = results.end_date

        base, quote = pair.split("/")

        initial_balance_a = r.get("initial_balance_a", 0)
        initial_balance_b = r.get("initial_balance_b", 0)

        lines = []
        lines.append("=" * 64)
        lines.append(" " * 16 + "BACKTEST RESULTS")
        lines.append("=" * 64)
        lines.append("")
        lines.append("Configuration:")
        lines.append(f"  Pair:           {pair}")
        lines.append(f"  Initial A:      {initial_balance_a}")
        lines.append(f"  Initial B:      {initial_balance_b}")
        lines.append("")
        lines.append("Period:")
        lines.append(f"  Start:          {start_date}")
        lines.append(f"  End:            {end_date}")
        lines.append(f"  Interval:       {results.interval}")
        lines.append("")
        lines.append("Results:")
        lines.append("  " + "-" * 50)

        profit_a = r["profit_a"]
        profit_b = r["profit_b"]
        profit_a_pct = (
            (profit_a / initial_balance_a * 100) if initial_balance_a > 0 else 0
        )
        profit_b_pct = (
            (profit_b / initial_balance_b * 100) if initial_balance_b > 0 else 0
        )

        lines.append("  INDIVIDUAL TOKEN BALANCES")
        lines.append(
            f"  {base}:      {r['final_balance_a']:.4f} ({profit_a:+.4f}, {profit_a_pct:+.1f}%)"
        )
        lines.append(
            f"  {quote}:  {r['final_balance_b']:.4f} ({profit_b:+.4f}, {profit_b_pct:+.1f}%)"
        )
        lines.append("  " + "-" * 50)

        prices = results.prices
        if prices is not None and not prices.empty:
            final_price = float(prices["Close"].iloc[-1])
            initial_price = float(prices["Close"].iloc[0])

            total_a_initial = initial_balance_a + (initial_balance_b / initial_price)
            total_a_final = r["final_balance_a"] + (r["final_balance_b"] / final_price)
            total_a_pct = (
                ((total_a_final - total_a_initial) / total_a_initial * 100)
                if total_a_initial > 0
                else 0
            )

            total_b_initial = (initial_balance_a * initial_price) + initial_balance_b
            total_b_final = (r["final_balance_a"] * final_price) + r["final_balance_b"]
            total_b_pct = (
                ((total_b_final - total_b_initial) / total_b_initial * 100)
                if total_b_initial > 0
                else 0
            )

            lines.append("  TOTAL NORMALIZED (at dataset price)")
            lines.append(
                f"  In {base}:  {total_a_final:.4f} ({total_a_final - total_a_initial:+.4f}, {total_a_pct:+.1f}%)"
            )
            lines.append(
                f"  In {quote}: {total_b_final:.4f} ({total_b_final - total_b_initial:+.4f}, {total_b_pct:+.1f}%)"
            )
            lines.append("  " + "-" * 50)

        lines.append("")
        lines.append(f"  Total Trades:        {r['total_trades']}")
        lines.append(f"  Buy Trades:           {r['buy_trades']}")
        lines.append(f"  Sell Trades:          {r['sell_trades']}")
        lines.append("")
        lines.append(f"  Avg Trade Size:      {r['avg_trade_size']:.4f}")
        lines.append("")
        lines.append("=" * 64)

        return "\n".join(lines)

    def export_csv(self, results: BacktestResults, path: str):
        """Export trade history to CSV."""
        trades = results.results["trades"]

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "side",
                    "amount",
                    "price",
                    "timestamp",
                    "balance_a_after",
                    "balance_b_after",
                ]
            )

            for trade in trades:
                writer.writerow(
                    [
                        trade.id,
                        trade.side,
                        trade.amount,
                        trade.price,
                        trade.timestamp,
                        trade.balance_a_after,
                        trade.balance_b_after,
                    ]
                )

    def export_charts(self, results: BacktestResults, output_dir: str):
        """Generate equity curve and trade distribution charts."""
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            raise ImportError(
                "matplotlib is required for chart generation. "
                "Install with: pip install matplotlib"
            )

        import os

        os.makedirs(output_dir, exist_ok=True)

        self._export_equity_curve(results, output_dir)
        self._export_trade_distribution(results, output_dir)

    def _export_equity_curve(self, results: BacktestResults, output_dir: str):
        """Export equity curve chart."""
        import matplotlib.pyplot as plt

        equity_curve = results.results["equity_curve"]

        if not equity_curve:
            return

        timestamps = [p["timestamp"] for p in equity_curve]
        equity = [p["total_equity_a"] for p in equity_curve]

        plt.figure(figsize=(12, 6))
        plt.plot(timestamps, equity)
        plt.title(f"Equity Curve - {results.pair}")
        plt.xlabel("Date")
        plt.ylabel("Total Equity (TOKEN_A)")
        plt.grid(True)
        plt.tight_layout()

        plt.savefig(f"{output_dir}/equity_curve.png")
        plt.close()

    def _export_trade_distribution(self, results: BacktestResults, output_dir: str):
        """Export trade size distribution chart."""
        import matplotlib.pyplot as plt

        trades = results.results["trades"]

        if not trades:
            return

        trade_sizes = [t.amount for t in trades]

        plt.figure(figsize=(10, 6))
        plt.hist(trade_sizes, bins=20, edgecolor="black")
        plt.title(f"Trade Size Distribution - {results.pair}")
        plt.xlabel("Trade Size (TOKEN_A)")
        plt.ylabel("Frequency")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        plt.savefig(f"{output_dir}/trade_distribution.png")
        plt.close()
