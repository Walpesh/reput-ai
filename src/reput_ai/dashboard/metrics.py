"""Pure aggregation helpers for the dashboard (kept Streamlit-free so they are unit-testable)."""

from __future__ import annotations

from collections import Counter
from typing import Any

NEGATIVE_MAX_RATING = 3
POSITIVE_MIN_RATING = 4


def compute_kpis(reviews: list[dict[str, Any]]) -> dict[str, float | int]:
    """Compute headline KPIs from review payloads returned by the API."""
    total = len(reviews)
    if total == 0:
        return {
            "total": 0,
            "avg_rating": 0.0,
            "negative": 0,
            "positive": 0,
            "pending": 0,
        }

    ratings = [int(r.get("rating", 0)) for r in reviews]
    return {
        "total": total,
        "avg_rating": round(sum(ratings) / total, 2),
        "negative": sum(1 for r in ratings if r <= NEGATIVE_MAX_RATING),
        "positive": sum(1 for r in ratings if r >= POSITIVE_MIN_RATING),
        "pending": sum(1 for r in reviews if r.get("status") == "NEW"),
    }


def rating_distribution(reviews: list[dict[str, Any]]) -> dict[int, int]:
    """Rating counts keyed 1..5 (missing ratings are ignored)."""
    counter: Counter[int] = Counter(
        int(r["rating"]) for r in reviews if r.get("rating") is not None
    )
    return {rating: counter.get(rating, 0) for rating in range(1, 6)}


def filter_reviews(
    reviews: list[dict[str, Any]],
    *,
    branch_ids: set[str] | None = None,
    min_rating: int | None = None,
    max_rating: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Apply dashboard sidebar filters to a review list."""
    result = reviews
    if branch_ids:
        result = [r for r in result if str(r.get("branch_id")) in branch_ids]
    if min_rating is not None:
        result = [r for r in result if int(r.get("rating", 0)) >= min_rating]
    if max_rating is not None:
        result = [r for r in result if int(r.get("rating", 0)) <= max_rating]
    if status:
        result = [r for r in result if r.get("status") == status]
    return result
