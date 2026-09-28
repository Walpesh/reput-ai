from celery import Celery
from reput_ai.config import settings

celery_app = Celery(
    "reput_ai_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "reput_ai.workers.tasks.scraper",
        "reput_ai.workers.tasks.ai_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "poll-branches-reviews-periodically": {
            "task": "reput_ai.workers.tasks.scraper.poll_all_branches_reviews",
            "schedule": float(settings.SCRAPER_POLL_INTERVAL_MINUTES * 60),
        },
    },
)
