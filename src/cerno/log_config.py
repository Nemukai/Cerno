from __future__ import annotations

import logging

from cerno.config import Settings


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    else:
        root.setLevel(level)
