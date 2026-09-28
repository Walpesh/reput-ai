"""Bridge between the synchronous Celery worker context and the async core."""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Execute a coroutine from a synchronous Celery task.

    Tests replace this helper with an inline awaiter, so that eager Celery tasks
    (``task_always_eager``) can run inside the pytest event loop.
    """
    return asyncio.run(coro)
