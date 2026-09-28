"""Celery application: broker wiring, queues and the periodic beat schedule."""

from celery import Celery
from celery.schedules import crontab

from reput_ai.config import resolve_scraper_poll_interval_minutes, settings

celery_app = Celery(
    "reput_ai_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "reput_ai.workers.tasks.scraper",
        "reput_ai.workers.tasks.ai_tasks",
        "reput_ai.workers.tasks.billing_tasks",
    ],
)

#: Effective polling interval, clamped into the supported 15-30 minute window.
SCRAPER_POLL_INTERVAL_MINUTES = resolve_scraper_poll_interval_minutes()

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    result_expires=3600,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "reput_ai.workers.tasks.scraper.*": {"queue": "scraper"},
        "reput_ai.workers.tasks.ai_tasks.*": {"queue": "ai"},
        "reput_ai.workers.tasks.billing_tasks.*": {"queue": "billing"},
    },
    beat_schedule={
        "poll-branches-reviews-periodically": {
            "task": "reput_ai.workers.tasks.scraper.poll_all_branches_reviews",
            "schedule": float(SCRAPER_POLL_INTERVAL_MINUTES * 60),
        },
        "process-new-reviews-with-ai": {
            "task": "reput_ai.workers.tasks.ai_tasks.process_new_reviews",
            "schedule": float(settings.AI_PROCESSING_INTERVAL_MINUTES * 60),
        },
        "billing-lifecycle-hourly": {
            "task": "reput_ai.workers.tasks.billing_tasks.run_billing_lifecycle",
            "schedule": crontab(minute=15),
        },
    },
)

