from arq import cron
from arq.connections import RedisSettings
from libs.common.settings import settings
from libs.common.log import logger

async def ping(ctx):
    logger.info("Worker alive.")

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [ping]
    cron_jobs = [cron(ping, second=0, minute="*/10")]
