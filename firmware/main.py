"""Boot the board agent after uploading config.json and virtual_esp_board/."""

import logging
import time

from virtual_esp_board.agent import run
from virtual_esp_board.settings import Settings

# Delay before resetting after an unexpected error, to avoid a tight boot loop.
RESET_DELAY_SECONDS = 10

logger = logging.getLogger("main")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    # Configuration errors propagate so they are visible on the console.
    settings = Settings.from_file()
    try:
        run(settings)
    except Exception:
        # The board is unattended: recover from bugs instead of staying offline.
        # KeyboardInterrupt is not an Exception, so Ctrl-C still reaches the REPL.
        logger.exception("Agent stopped unexpectedly; resetting")
        time.sleep(RESET_DELAY_SECONDS)
        import machine

        machine.reset()


if __name__ == "__main__":
    main()
