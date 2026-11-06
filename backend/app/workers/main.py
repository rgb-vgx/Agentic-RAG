"""RQ worker entrypoint.

Run with:
    rq worker ingestion --url redis://localhost:6379/0
"""

from redis import Redis

from app.config import settings

redis_connection = Redis.from_url(settings.redis_url)
