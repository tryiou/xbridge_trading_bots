#!/usr/bin/env python3
"""Comprehensive backtest analysis tool."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from tabulate import tabulate


@dataclass
class BacktestData:
    """Container for loaded backtest data."""

    summary: dict
    balances: pd.DataFrame
    trades: pd.DataFrame
    orderbook: pd.DataFrame


class BacktestAnalyzer:
    """Comprehensive backtest analysis tool."""

    def __init__(self, result_dir: str):
        self.result_dir = Path(result_dir)
        self.data: BacktestData | None = None

    def load_data(self) -> BacktestData:
        """Load all backtest data files."""
        summary_path = self.result_dir / "summary.json"
        balances_path = self.result_dir / "balances.csv"
        trades_path = self.result_dir / "trades.csv"
        orderbook_path = self.result_dir / "orderbook.csv"

        if not summary_path.exists():
            raise FileNotFoundError(f"summary.json not found in {self.result_dir}")

        with open(summary_path) as f:
            summary = json.load(f)

        balances = (
            pd.read_csv(balances_path) if balances_path.exists() else pd.DataFrame()
        )
        trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
        orderbook = (
            pd.read_csv(orderbook_path) if orderbook_path.exists() else pd.DataFrame()
        )

        self.data = BacktestData(
            summary=summary, balances=balances, trades=trades, orderbook=orderbook
        )
        return self.data

    def analyze(self) -> dict:
        """Run complete analysis and return metrics."""
        if self.data is None:
            self.load_data()

        return {
            "header": self._header_analysis(),
            "price": self._price_analysis(),
            "portfolio": self._portfolio_analysis(),
            "buyhold": self._vs_buyhold(),
            "trades": self._trade_analysis(),
            "orders": self._order_analysis(),
            "risk": self._risk_analysis(),
            "concentration": self._concentration_analysis(),
            "reserve": self._reserve_analysis(),
        }

    def _header_analysis(self) -> dict:
        """Extract header information."""
        cfg = self.data.summary["config"]
        pair_cfg = cfg["pair_config"]

        start = cfg["start_date"]
        end = cfg["end_date"]
        intervals = len(self.data.balances) if not self.data.balances.empty else 0

        return {
            "pair": cfg["pair"],
            "start_date": start,
            "end_date": end,
            "intervals": intervals,
            "mode": pair_cfg.get("order_sizing_mode", "unknown"),
            "initial_balance_a": pair_cfg.get("initial_balance_a"),
            "initial_balance_b": pair_cfg.get("initial_balance_b"),
            "min_reserve_percent": pair_cfg.get("inventory", {}).get(
                "min_reserve_percent", 0.10
            ),
            "concentration_sensitivity": pair_cfg.get("inventory", {}).get(
                "concentration_sensitivity", 2.0
            ),
        }

    def _price_analysis(self) -> dict:
        """Analyze price movements."""
        if self.data.balances.empty:
            return {}

        prices = self.data.balances["price_close"]
        returns = prices.pct_change(fill_method=None).dropna()

        return {
            "start": prices.iloc[0],
            "end": prices.iloc[-1],
            "high": prices.max(),
            "low": prices.min(),
            "change_percent": ((prices.iloc[-1] / prices.iloc[0]) - 1) * 100,
            "volatility_percent": returns.std() * 100 if len(returns) > 0 else 0,
            "avg_price": prices.mean(),
        }

    def _portfolio_analysis(self) -> dict:
        """Analyze portfolio performance."""
        cfg = self.data.summary["config"]
        pair_cfg = cfg["pair_config"]
        summary = self.data.summary["summary"]

        init_a = pair_cfg.get("initial_balance_a")
        init_b = pair_cfg.get("initial_balance_b")
        final_a = summary["final_balance_a"]
        final_b = summary["final_balance_b"]

        price_info = self._price_analysis()
        start_price = price_info.get("start", 1)
        end_price = price_info.get("end", 1)

        total_init_a = init_a + (init_b / start_price)
        total_final_a = final_a + (final_b / end_price)
        return_a_percent = (
            ((total_final_a / total_init_a) - 1) * 100 if total_init_a != 0 else 0
        )

        total_init_b = (init_a * start_price) + init_b
        total_final_b = (final_a * end_price) + final_b
        return_b_percent = (
            ((total_final_b / total_init_b) - 1) * 100 if total_init_b != 0 else 0
        )

        return {
            "initial_a": init_a,
            "initial_b": init_b,
            "initial_total_a": total_init_a,
            "initial_total_b": total_init_b,
            "final_a": final_a,
            "final_b": final_b,
            "final_total_a": total_final_a,
            "final_total_b": total_final_b,
            "return_a_percent": return_a_percent,
            "return_b_percent": return_b_percent,
        }

    def _vs_buyhold(self) -> dict:
        """Compare to passive buy & hold strategy."""
        cfg = self.data.summary["config"]
        pair_cfg = cfg["pair_config"]

        init_a = pair_cfg.get("initial_balance_a", 5.0)
        init_b = pair_cfg.get("initial_balance_b", 270.0)

        price_info = self._price_analysis()
        start_price = price_info.get("start", 1)
        end_price = price_info.get("end", 1)

        portfolio = self._portfolio_analysis()

        initial_value_a = init_a + (init_b / start_price)
        price_change_ratio = end_price / start_price
        buyhold_final_a = initial_value_a * price_change_ratio

        strategy_a = portfolio["final_total_a"]
        diff_a = strategy_a - buyhold_final_a
        diff_percent_a = (diff_a / buyhold_final_a) * 100 if buyhold_final_a != 0 else 0

        initial_value_b = (init_a * start_price) + init_b
        buyhold_final_b = initial_value_b * price_change_ratio

        strategy_b = portfolio["final_total_b"]
        diff_b = strategy_b - buyhold_final_b
        diff_percent_b = (diff_b / buyhold_final_b) * 100 if buyhold_final_b != 0 else 0

        return {
            "buyhold_total_a": buyhold_final_a,
            "strategy_total_a": strategy_a,
            "diff_a": diff_a,
            "diff_percent_a": diff_percent_a,
            "outperformed_a": diff_a > 0,
            "buyhold_total_b": buyhold_final_b,
            "strategy_total_b": strategy_b,
            "diff_b": diff_b,
            "diff_percent_b": diff_percent_b,
            "outperformed_b": diff_b > 0,
        }

    def _trade_analysis(self) -> dict:
        """Analyze trade statistics."""
        if self.data.trades.empty:
            return {}

        trades = self.data.trades
        summary = self.data.summary["summary"]

        return {
            "total": len(trades),
            "buys": int(summary.get("buy_trades", 0)),
            "sells": int(summary.get("sell_trades", 0)),
            "avg_size": trades["amount"].mean(),
            "total_volume_a": trades["amount"].sum(),
            "volume_a_currency": (trades["amount"] * trades["price"]).sum(),
        }

    def _order_analysis(self) -> dict:
        """Analyze order statistics."""
        if self.data.orderbook.empty:
            return {}

        orders = self.data.orderbook

        created = len(orders[orders["status"] == "open"])

        filled = 0 if self.data.trades.empty else len(self.data.trades)

        canceled = 0

        fill_rate = filled / created if created > 0 else 0

        return {
            "created": created,
            "filled": filled,
            "canceled": canceled,
            "fill_rate_percent": fill_rate * 100,
        }

    def _risk_analysis(self) -> dict:
        """Analyze risk metrics."""
        if self.data.balances.empty:
            return {}

        balances = self.data.balances

        value_col = (
            "total_value_base"
            if "total_value_base" in balances.columns
            else "total_value"
        )
        equity = balances[value_col]

        running_max = equity.expanding().max()
        drawdown = (equity - running_max) / running_max
        max_drawdown = drawdown.min() * 100

        returns = equity.pct_change(fill_method=None).dropna()
        volatility = returns.std() * 100 if len(returns) > 0 else 0

        return_vol = abs(max_drawdown) if max_drawdown != 0 else 1
        calmar = (
            (self._portfolio_analysis().get("return_a_percent", 0) / return_vol)
            if return_vol > 0
            else 0
        )

        return {
            "max_drawdown_percent": max_drawdown,
            "volatility_percent": volatility,
            "calmar_ratio": calmar,
        }

    def _concentration_analysis(self) -> dict:
        """Analyze token concentration over time."""
        if self.data.balances.empty:
            return {}

        ratios = self.data.balances["ratio_a"]

        return {
            "min_ratio_a": ratios.min(),
            "max_ratio_a": ratios.max(),
            "avg_ratio_a": ratios.mean(),
            "time_above_70_percent": (ratios > 0.70).sum() / len(ratios) * 100,
            "time_below_30_percent": (ratios < 0.30).sum() / len(ratios) * 100,
            "time_40_60_percent": ((ratios >= 0.40) & (ratios <= 0.60)).sum()
            / len(ratios)
            * 100,
        }

    def _reserve_analysis(self) -> dict:
        """Analyze reserve maintenance."""
        cfg = self.data.summary["config"]
        pair_cfg = cfg["pair_config"]
        min_reserve = pair_cfg.get("inventory", {}).get("min_reserve_percent", 0.10)

        if self.data.balances.empty:
            return {}

        init_a = pair_cfg.get("initial_balance_a")
        init_b = pair_cfg.get("initial_balance_b")

        reserve_a = init_a * min_reserve
        reserve_b = init_b * min_reserve

        balances = self.data.balances
        below_reserve_a = (balances["balance_a"] < reserve_a).sum()
        below_reserve_b = (balances["balance_b"] < reserve_b).sum()
        total_points = len(balances)

        return {
            "min_reserve_percent": min_reserve,
            "reserve_a_absolute": reserve_a,
            "reserve_b_absolute": reserve_b,
            "min_balance_a": balances["balance_a"].min(),
            "min_balance_b": balances["balance_b"].min(),
            "below_reserve_a_count": int(below_reserve_a),
            "below_reserve_b_count": int(below_reserve_b),
            "below_reserve_a_percent": below_reserve_a / total_points * 100,
            "below_reserve_b_percent": below_reserve_b / total_points * 100,
        }

    def print_report(self) -> None:
        """Print comprehensive CLI report."""
        if self.data is None:
            self.load_data()

        metrics = self.analyze()
        h = metrics["header"]
        p = metrics["price"]
        pf = metrics["portfolio"]
        bh = metrics["buyhold"]
        t = metrics["trades"]
        o = metrics["orders"]
        r = metrics["risk"]
        c = metrics["concentration"]
        res = metrics["reserve"]

        pair = h["pair"]
        base_token, quote_token = pair.split("/") if "/" in pair else (pair, "UNKNOWN")

        ret_a = pf["return_a_percent"]
        ret_b = pf["return_b_percent"]

        print()
        print("══════════════════════════════════════════════════════════════════")
        print("                    BACKTEST RESULTS")
        print("══════════════════════════════════════════════════════════════════")
        print(f" {pair} | {h['start_date']} → {h['end_date']} | {h['mode']}")
        print()

        print("──────────────────────────────────────────────────────────────────")
        print(" VERDICT")
        print("──────────────────────────────────────────────────────────────────")
        v_a = "✓ OUTPERFORM" if bh["outperformed_a"] else "✗ UNDERPERFORM"
        v_b = "✓ OUTPERFORM" if bh["outperformed_b"] else "✗ UNDERPERFORM"
        verdict_table = [
            [
                f"{base_token}:",
                f"{ret_a:+.2f}%",
                f"vs B&H {bh['diff_percent_a']:+.2f}%",
                v_a,
            ],
            [
                f"{quote_token}:",
                f"{ret_b:+.2f}%",
                f"vs B&H {bh['diff_percent_b']:+.2f}%",
                v_b,
            ],
        ]
        print(
            tabulate(
                verdict_table,
                tablefmt="simple",
                colalign=("left", "right", "right", "center"),
            )
        )
        print()

        print("──────────────────────────────────────────────────────────────────")
        print(" PRICE")
        print("──────────────────────────────────────────────────────────────────")
        price_table = [
            ["Start", "End", "Change", "High", "Low", "Volatility"],
            [
                f"{p['start']:.2f}",
                f"{p['end']:.2f}",
                f"{p['change_percent']:+.1f}%",
                f"{p['high']:.2f}",
                f"{p['low']:.2f}",
                f"{p['volatility_percent']:.1f}%",
            ],
        ]
        print(
            tabulate(
                price_table,
                tablefmt="simple",
                colalign=("left", "right", "right", "right", "right", "right"),
            )
        )
        print()

        print("──────────────────────────────────────────────────────────────────")
        print(" PORTFOLIO")
        print("──────────────────────────────────────────────────────────────────")
        print(" REAL BALANCE:")
        balance_table = [
            ["", base_token, quote_token],
            ["Initial", f"{pf['initial_a']:.4f}", f"{pf['initial_b']:.2f}"],
            ["Final", f"{pf['final_a']:.4f}", f"{pf['final_b']:.2f}"],
        ]
        print(tabulate(balance_table, tablefmt="simple", headers="firstrow"))

        print(f"\n PROJECTED @ END PRICE ({p['end']:.2f}):")
        projected_table = [
            ["", base_token, quote_token],
            ["Total", f"{pf['final_total_a']:.4f}", f"{pf['final_total_b']:.2f}"],
            ["Return", f"{ret_a:+.2f}%", f"{ret_b:+.2f}%"],
        ]
        print(tabulate(projected_table, tablefmt="simple", headers="firstrow"))
        print()

        print("──────────────────────────────────────────────────────────────────")
        print(" VS BUY & HOLD")
        print("──────────────────────────────────────────────────────────────────")
        v_a = "✓" if bh["outperformed_a"] else "✗"
        v_b = "✓" if bh["outperformed_b"] else "✗"
        bh_table = [
            ["", f"BASE ({base_token})", f"QUOTE ({quote_token})"],
            [
                "Buy&Hold",
                f"{bh['buyhold_total_a']:.4f}",
                f"{bh['buyhold_total_b']:.2f}",
            ],
            [
                "Strategy",
                f"{bh['strategy_total_a']:.4f}",
                f"{bh['strategy_total_b']:.2f}",
            ],
            [
                "Delta",
                f"{bh['diff_a']:+.4f} ({bh['diff_percent_a']:+.2f}%)",
                f"{bh['diff_b']:+.2f} ({bh['diff_percent_b']:+.2f}%)",
            ],
            [
                "Result",
                f"{v_a} OUTPERFORM" if bh["outperformed_a"] else f"{v_a} UNDERPERFORM",
                f"{v_b} OUTPERFORM" if bh["outperformed_b"] else f"{v_b} UNDERPERFORM",
            ],
        ]
        print(tabulate(bh_table, tablefmt="simple", headers="firstrow"))
        print()

        print("──────────────────────────────────────────────────────────────────")
        print(" TRADING")
        print("──────────────────────────────────────────────────────────────────")
        trading_table = [
            ["Metric", "Value"],
            ["Total Trades", f"{t['total']}"],
            ["Buys", f"{t['buys']}"],
            ["Sells", f"{t['sells']}"],
            ["Avg Size", f"{t['avg_size']:.4f} {base_token}"],
            ["Total Volume", f"{t['total_volume_a']:.2f} {base_token}"],
            ["Orders Created", f"{o['created']}"],
            ["Orders Filled", f"{o['filled']}"],
            ["Fill Rate", f"{o['fill_rate_percent']:.1f}%"],
        ]
        print(tabulate(trading_table, tablefmt="simple", headers="firstrow"))
        print()

        print("──────────────────────────────────────────────────────────────────")
        print(" RISK")
        print("──────────────────────────────────────────────────────────────────")
        risk_table = [
            ["Metric", "Value"],
            ["Max Drawdown", f"{r['max_drawdown_percent']:.2f}%"],
            ["Volatility", f"{r['volatility_percent']:.2f}%"],
            ["Calmar Ratio", f"{r['calmar_ratio']:.2f}"],
        ]
        print(tabulate(risk_table, tablefmt="simple", headers="firstrow"))

        print(f"\n CONCENTRATION ({base_token}):")
        conc_table = [
            ["Metric", "Value"],
            ["Min Ratio", f"{c['min_ratio_a']:.1%}"],
            ["Max Ratio", f"{c['max_ratio_a']:.1%}"],
            ["Avg Ratio", f"{c['avg_ratio_a']:.1%}"],
            ["Time 40-60%", f"{c['time_40_60_percent']:.1f}%"],
            ["Time <30%", f"{c['time_below_30_percent']:.1f}%"],
            ["Time >70%", f"{c['time_above_70_percent']:.1f}%"],
        ]
        print(tabulate(conc_table, tablefmt="simple", headers="firstrow"))

        print("\n RESERVE:")
        reserve_table = [
            ["Metric", base_token, quote_token],
            [
                "Min Reserve",
                f"{res['reserve_a_absolute']:.4f} ({res['min_reserve_percent'] * 100:.0f}%)",
                f"{res['reserve_b_absolute']:.2f} ({res['min_reserve_percent'] * 100:.0f}%)",
            ],
            [
                "Actual Min",
                f"{res['min_balance_a']:.4f}",
                f"{res['min_balance_b']:.2f}",
            ],
            [
                "Below Reserve",
                f"{res['below_reserve_a_count']} ({res['below_reserve_a_percent']:.1f}%)",
                f"{res['below_reserve_b_count']} ({res['below_reserve_b_percent']:.1f}%)",
            ],
        ]
        print(tabulate(reserve_table, tablefmt="simple", headers="firstrow"))
        print()

    def to_json(self) -> dict:
        """Return analysis as JSON."""
        if self.data is None:
            self.load_data()
        return self.analyze()


def main():
    parser = argparse.ArgumentParser(description="Analyze backtest results")
    parser.add_argument("result_dir", help="Path to backtest result directory")
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of CLI report"
    )
    args = parser.parse_args()

    analyzer = BacktestAnalyzer(args.result_dir)

    try:
        if args.json:
            result = analyzer.to_json()
            print(json.dumps(result, indent=2))
        else:
            analyzer.print_report()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
