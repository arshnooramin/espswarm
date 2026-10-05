"""Boot the board agent after uploading config.json and espswarm_agent/."""

import logging
import os
import time

from espswarm_agent.agent import run
from espswarm_agent.settings import Settings
from espswarm_agent.targets import detect

# Delay before resetting after an unexpected error, to avoid a tight boot loop.
RESET_DELAY_SECONDS = 10

logger = logging.getLogger("main")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    # Configuration and unsupported-chip errors propagate to the console.
    settings = Settings.from_file()
    target = detect(os.uname().machine)
    try:
        run(settings, target)
    except Exception:
        # The board is unattended: recover from bugs instead of staying offline.
        # KeyboardInterrupt is not an Exception, so Ctrl-C still reaches the REPL.
        logger.exception("Agent stopped unexpectedly; resetting")
        time.sleep(RESET_DELAY_SECONDS)
        import machine

        machine.reset()


if __name__ == "__main__":
    main()
