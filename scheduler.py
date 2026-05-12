import os
import signal
import subprocess
import sys
import time
import logging
import schedule
from logging.handlers import RotatingFileHandler

_HERE = os.path.dirname(os.path.abspath(__file__))

_fh = RotatingFileHandler(
    os.path.join(_HERE, "outreach.log"), maxBytes=5 * 1024 * 1024, backupCount=3
)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.root.setLevel(logging.INFO)
logging.root.addHandler(_fh)
logging.root.addHandler(_sh)
log = logging.getLogger(__name__)


def _smtp_preflight() -> None:
    log.info("Running SMTP pre-flight check...")
    r = subprocess.run(
        ["python3", os.path.join(_HERE, "outreach.py"), "--smtp-test"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.error("SMTP pre-flight failed — check EMAIL_FROM / EMAIL_PASSWORD in .env")
        if r.stderr.strip():
            log.error(r.stderr.strip())
        sys.exit(1)
    log.info("SMTP pre-flight passed.")


def run_outreach() -> None:
    log.info("=== Daily outreach run starting ===")
    for cmd in ["--send", "--follow-ups"]:
        try:
            r = subprocess.run(
                ["python3", os.path.join(_HERE, "outreach.py"), cmd],
                capture_output=True, text=True,
            )
            if r.stdout.strip():
                log.info(r.stdout.strip())
            if r.returncode != 0:
                log.error("outreach.py %s exited %d: %s", cmd, r.returncode, r.stderr.strip())
            else:
                log.info("outreach.py %s completed successfully", cmd)
        except Exception as exc:
            log.exception("Unexpected error running outreach.py %s: %s", cmd, exc)
    log.info("=== Done ===")


_running = True


def _handle_stop(signum, frame):
    global _running
    log.info("Received signal %d — shutting down.", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)

_smtp_preflight()
schedule.every().day.at("08:00").do(run_outreach)
log.info("Scheduler started — outreach runs daily at 08:00")

while _running:
    schedule.run_pending()
    time.sleep(60)

log.info("Scheduler stopped cleanly.")
sys.exit(0)
