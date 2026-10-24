"""Entry point: ``python -m ttmedia [--config PATH]``."""

from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_CONFIG_PATH, load_config
from .errors import ConfigError, SdkError, TTMediaError
from .log import get_logger, setup_logging

log = get_logger("ttmedia")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ttmedia", description="Simple TT Media — TeamTalk media player bot")
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH,
                        help=f"path to the config file (default: {DEFAULT_CONFIG_PATH})")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        setup_logging("INFO")
        log.error("%s", exc)
        return 2

    setup_logging(cfg.log_level, cfg.log_file or None)

    from .app import TTMediaApp
    try:
        app = TTMediaApp(cfg)
    except (SdkError, ConfigError) as exc:
        log.error("%s", exc)
        return 3
    except TTMediaError as exc:
        log.error("%s", exc)
        return 1

    try:
        app.run()
    except KeyboardInterrupt:
        log.info("Interrupted; shutting down.")
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
