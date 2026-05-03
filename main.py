"""
main.py — Entry point for the K8s AI Agent.

Usage:
  python main.py               # Connect to real cluster, poll every POLL_INTERVAL_SECONDS
  python main.py --simulate    # Use fake unhealthy pods (no cluster needed)
  python main.py --once        # Run one cycle and exit (useful for testing)

Environment variables (see config.py for the full list):
  LLM_BASE_URL           Override the LLM server URL
  LLM_MODEL              Model name served at LLM_BASE_URL
  POLL_INTERVAL_SECONDS  How often to scan (default: 120)
  SIMULATE               Set to "true" to enable simulation mode
"""

import argparse
import signal
import sys
import time

from rich.console import Console

import config
from agent import run_cycle

console = Console()

# ─── CLI args ─────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(
    description="K8s AI Agent — detects and diagnoses unhealthy pods using an LLM"
)
parser.add_argument(
    "--simulate",
    action="store_true",
    help="Use simulated (fake) unhealthy pods — no live cluster needed",
)
parser.add_argument(
    "--once",
    action="store_true",
    help="Run a single cycle and exit instead of looping",
)
args = parser.parse_args()

# CLI flag overrides the env-var setting
if args.simulate:
    config.SIMULATE = True

# ─── Graceful shutdown ────────────────────────────────────────────────────────

_running = True


def _handle_signal(sig, frame):
    global _running
    console.print("\n[bold yellow]⚠️  Shutdown signal received. Finishing current cycle…[/bold yellow]")
    _running = False


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ─── Banner ───────────────────────────────────────────────────────────────────

def _print_banner():
    mode = "[yellow]SIMULATION MODE[/yellow]" if config.SIMULATE else "[green]LIVE CLUSTER MODE[/green]"
    console.print(
        f"""
[bold cyan]╔══════════════════════════════════════════════════════════╗
║          🤖  K8s AI Agent — Pod Health Monitor           ║
╚══════════════════════════════════════════════════════════╝[/bold cyan]
  LLM endpoint : [cyan]{config.LLM_BASE_URL}[/cyan]
  Model        : [cyan]{config.LLM_MODEL}[/cyan]
  Poll interval: [cyan]{config.POLL_INTERVAL_SECONDS}s[/cyan]
  Threshold    : [cyan]{config.UNHEALTHY_THRESHOLD_SECONDS}s[/cyan]
  Mode         : {mode}
  State file   : [dim]{config.STATE_FILE}[/dim]
  Diagnosis log: [dim]{config.DIAGNOSIS_LOG_FILE}[/dim]

  Press [bold]Ctrl+C[/bold] to stop gracefully.
"""
    )


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    _print_banner()

    if args.once:
        run_cycle()
        console.print("[dim]--once flag set — exiting after single cycle.[/dim]")
        sys.exit(0)

    while _running:
        run_cycle()

        if not _running:
            break

        console.print(
            f"[dim]⏳ Next scan in {config.POLL_INTERVAL_SECONDS}s "
            "(Ctrl+C to stop) …[/dim]\n"
        )

        # Sleep in small chunks so Ctrl+C is responsive
        for _ in range(config.POLL_INTERVAL_SECONDS):
            if not _running:
                break
            time.sleep(1)

    console.print("[bold green]✅ Agent stopped cleanly.[/bold green]")


if __name__ == "__main__":
    main()
