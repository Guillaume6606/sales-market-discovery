import os
import sys

from loguru import logger

# Get log level from environment variable, default to INFO
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logger.remove()
logger.add(
    sys.stdout,
    level=LOG_LEVEL,
    enqueue=True,
    backtrace=True if LOG_LEVEL == "DEBUG" else False,
    diagnose=True if LOG_LEVEL == "DEBUG" else False,
)
