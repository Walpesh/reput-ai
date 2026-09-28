import logging
from reput_ai.workers.celery_app import celery_app

logger = logging.getLogger("reput_ai.workers.scraper")


@celery_app.task(name="reput_ai.workers.tasks.scraper.poll_all_branches_reviews")
def poll_all_branches_reviews() -> dict[str, str]:
    logger.info("Executing scheduled poll for all branch reviews (Yandex/2GIS/Google/Avito)...")
    return {"status": "success", "message": "Poll cycle completed"}


@celery_app.task(name="reput_ai.workers.tasks.scraper.scrape_branch_reviews")
def scrape_branch_reviews(branch_id: str) -> dict[str, str]:
    logger.info(f"Scraping reviews for branch {branch_id}...")
    return {"status": "success", "branch_id": branch_id}
