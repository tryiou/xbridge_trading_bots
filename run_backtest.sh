#!/bin/bash

# Backtest experiment runner - Compare order sizing curves and time periods
# Runs equal, growing_outward, growing_inward configurations across multiple periods

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR"

RESULTS_DIR="backtest_results"
mkdir -p $RESULTS_DIR

configs=(
	# "config/backtest_equal.yaml:equal"
	"config/backtest_growing_outward.yaml:growing_outward"
	# "config/backtest_growing_inward.yaml:growing_inward"
)

intervals=(
	"2025-06-01:2025-09-01:3month"
	# "2025-06-01:2025-12-01:6month"
	# "2025-01-01:2026-01-01:12month"
)

interval="1h"

echo "Running backtest experiments - Order Sizing Curves Comparison"
echo "============================================================"

for item in "${configs[@]}"; do
	config="${item%%:*}"
	name="${item##*:}"

	for period in "${intervals[@]}"; do
		start="${period%%:*}"
		rest="${period#*:}"
		end="${rest%%:*}"
		period_name="${rest##*:}"

		output_name="${period_name}_${name}"

		echo "Running: $output_name"
		echo "Config: $config"
		echo "Period: $start to $end"

		output=$(python backtesting/run_backtest.py "$config" "$start" "$end" "$interval" "$output_name")
		echo "$output"

		folder_path=$(echo "$output" | grep "^BACKTEST_OUTPUT_DIR:" | sed 's/^BACKTEST_OUTPUT_DIR://')
		if [ -n "$folder_path" ]; then
			echo "Analyzing: $folder_path"
			python backtesting/analyse_backtest.py "$folder_path/"
		else
			echo "ERROR: Could not determine output folder"
		fi

		echo "Done: $output_name"
		echo "---"
	done
done

echo "============================================================"
echo "All experiments complete!"
echo "Results in: $RESULTS_DIR/"
echo ""
echo "Files created:"
ls -la $RESULTS_DIR/
