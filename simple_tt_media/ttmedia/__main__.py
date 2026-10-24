"""Entry point: ``python -m ttmedia [--config PATH]``.

Loads configuration, starts the bot, and keeps it running until Ctrl-C.
"""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, DEFAULT_CONFIG_PATH, load_config
from .log import get_logger, setup_logging

log = get_logger("ttmedia")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ttmedia", description="Simple TT Media - TeamTalk media player bot")
    parser.add_argument(
        "--config", "-c", default=DEFAULT_CONFIG_PATH,
        help=f"path to config file (default: {DEFAULT_CONFIG_PATH})")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        setup_logging("INFO")
        log.error("%s", exc)
        return 2

    setup_logging(cfg.log_level)

    # Import the bot lazily so config errors above don't require the SDK.
    from .bot import MediaBot
    from .sdk import SdkNotFoundError

    try:
        bot = MediaBot(cfg)
    except SdkNotFoundError as exc:
        log.error("%s", exc)
        return 3
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    log.info("Starting Simple TT Media v%s", __import__("ttmedia").__version__)
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        bot.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
