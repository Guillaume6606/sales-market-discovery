from loguru import logger
import sys
import os

# Get log level from environment variable, default to INFO
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logger.remove()
logger.add(
    sys.stdout, 
    level=LOG_LEVEL, 
    enqueue=True, 
    backtrace=True if LOG_LEVEL == "DEBUG" else False, 
    diagnose=True if LOG_LEVEL == "DEBUG" else False
)
