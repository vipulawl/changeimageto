"""
Blogging Agent daemon — runs continuously, acts when needed.

No fixed schedule. Logic:
  - If topic queue < MIN_QUEUE_SIZE: run research
  - If queue has topics and daily write limit not hit: write + PR
  - Sleep POLL_INTERVAL_MINUTES between checks

Start:  python daemon.py
Stop:   Ctrl+C

Logs to stdout and logs/daemon.log
"""

import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import config
from storage.db import init_db, get_all_topics

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/daemon.log"),
    ],
)
log = logging.getLogger("daemon")

_stop = False


def _handle_signal(sig, frame):
    global _stop
    log.info("Shutdown signal received — stopping after current task.")
    _stop = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _queued_count() -> int:
    return len(get_all_topics(status="queued"))


def main():
    init_db()

    log.info("Blogging Agent daemon started")
    log.info(f"Provider: {config.PROVIDER} / Model: {config.MODEL}")
    log.info(f"Poll interval: {config.POLL_INTERVAL_MINUTES}m | "
             f"Min queue: {config.MIN_QUEUE_SIZE} | "
             f"Max articles/day: {config.MAX_ARTICLES_PER_DAY}")
    log.info(f"Approval mode: {config.APPROVAL_MODE}")

    articles_today = 0
    last_date = ""

    while not _stop:
        today = datetime.now().strftime("%Y-%m-%d")

        # Reset daily counter at midnight
        if today != last_date:
            if last_date:
                log.info(f"New day — resetting article counter (wrote {articles_today} yesterday)")
            articles_today = 0
            last_date = today

        queued = _queued_count()
        log.info(f"Queue check: {queued} topic(s) queued | {articles_today}/{config.MAX_ARTICLES_PER_DAY} written today")

        # ── Research if queue is running low ──────────────────────────────────
        if queued < config.MIN_QUEUE_SIZE:
            log.info(f"Queue below {config.MIN_QUEUE_SIZE} — running Research Agent...")
            try:
                from orchestrator import run_research
                run_research()
                queued = _queued_count()
                log.info(f"Research done. Queue now: {queued} topic(s)")
            except Exception as e:
                log.error(f"Research failed: {e}", exc_info=True)

        # ── Write next article if daily limit not reached ─────────────────────
        if queued > 0 and articles_today < config.MAX_ARTICLES_PER_DAY:
            log.info("Writing next article...")
            try:
                from orchestrator import run_write
                run_write()
                articles_today += 1
                log.info(f"Write complete. Articles today: {articles_today}/{config.MAX_ARTICLES_PER_DAY}")
            except Exception as e:
                log.error(f"Write failed: {e}", exc_info=True)
        elif articles_today >= config.MAX_ARTICLES_PER_DAY:
            log.info(f"Daily limit reached ({config.MAX_ARTICLES_PER_DAY}). Resuming tomorrow.")
        elif queued == 0:
            log.info("Queue still empty after research. Will retry next cycle.")

        # ── Sleep until next check ────────────────────────────────────────────
        if not _stop:
            log.info(f"Sleeping {config.POLL_INTERVAL_MINUTES} minutes...")
            for _ in range(config.POLL_INTERVAL_MINUTES * 60 // 5):
                if _stop:
                    break
                time.sleep(5)

    log.info("Daemon stopped.")


if __name__ == "__main__":
    main()
