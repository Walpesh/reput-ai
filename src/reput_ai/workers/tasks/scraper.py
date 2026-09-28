"""Periodic scraping of external review platforms (15-30 minute polling window)."""

import logging
from uuid import UUID

from reput_ai.config import resolve_scraper_poll_interval_minutes
from reput_ai.db.session import async_session_maker
from reput_ai.services.review_pipeline import list_serviceable_branch_ids, scrape_branch
from reput_ai.services.scraper import ScraperError, build_scraper
from reput_ai.workers.celery_app import celery_app
from reput_ai.workers.runtime import run_async

logger = logging.getLogger("reput_ai.workers.scraper")


@celery_app.task(name="reput_ai.workers.tasks.scraper.poll_all_branches_reviews")
def poll_all_branches_reviews() -> dict[str, object]:
    """Fan out one scraping task per active branch of a serviceable subscription."""
    branch_ids = run_async(list_serviceable_branch_ids(async_session_maker))
    for branch_id in branch_ids:
        scrape_branch_reviews.delay(str(branch_id))

    logger.info(
        "Polling cycle dispatched for %s branch(es); interval is %s minutes",
        len(branch_ids),
        resolve_scraper_poll_interval_minutes(),
    )
    return {"status": "success", "branches": len(branch_ids)}


@celery_app.task(name="reput_ai.workers.tasks.scraper.scrape_branch_reviews")
def scrape_branch_reviews(branch_id: str) -> dict[str, object]:
    """Fetch fresh reviews of a single branch and store the not-yet-known ones."""
    try:
        outcome = run_async(
            scrape_branch(async_session_maker, UUID(branch_id), build_scraper)
        )
    except ScraperError as exc:
        # Transient platform failures must not break the beat cycle: the next
        # polling tick (15-30 minutes) automatically retries the branch.
        logger.error("Scraping branch %s failed: %s", branch_id, exc)
        return {"status": "error", "branch_id": branch_id, "error": str(exc)}

    return outcome.as_dict()

