import asyncio
import os
import signal
import sys


def run_cli(start_func):
    """
    A wrapper to run command-line interface applications with standardized
    signal handling and graceful shutdown on KeyboardInterrupt.

    Args:
        start_func (callable): The main function of the CLI application to run.
    """
    # Ensure proper event loop policy for Windows
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def handle_sigint(signum, frame):
        """Signal handler that raises KeyboardInterrupt to stop the application."""
        raise KeyboardInterrupt

    # Set up the signal handler for SIGINT (Ctrl+C)
    signal.signal(signal.SIGINT, handle_sigint)

    try:
        start_func()
    except KeyboardInterrupt:
        print("Caught shutdown signal - exiting gracefully")
        sys.exit(1)
