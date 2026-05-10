import schedule
import time
import subprocess
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("outreach.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

def run_outreach():
    log.info("=== Daily outreach run starting ===")
    for cmd in ["--send", "--follow-ups"]:
        result = subprocess.run(
            ["python3", "outreach.py", cmd],
            capture_output=True, text=True
        )
        log.info(result.stdout)
        if result.returncode != 0:
            log.error(result.stderr)
    log.info("=== Done ===")

schedule.every().day.at("08:00").do(run_outreach)

log.info("Scheduler started — outreach runs daily at 08:00")
while True:
    schedule.run_pending()
    time.sleep(60)
