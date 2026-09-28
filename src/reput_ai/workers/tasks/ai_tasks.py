import asyncio
import logging
from reput_ai.workers.celery_app import celery_app
from reput_ai.llm.client import LLMClient
from reput_ai.db.models.branch import ToneOfVoice

logger = logging.getLogger("reput_ai.workers.ai_tasks")


@celery_app.task(name="reput_ai.workers.tasks.ai_tasks.generate_review_reply_task")
def generate_review_reply_task(review_id: str, review_text: str, tone_of_voice: str) -> dict[str, str]:
    logger.info(f"Generating AI reply for review {review_id} with tone {tone_of_voice}")
    client = LLMClient()
    t_voice = ToneOfVoice(tone_of_voice)
    reply = asyncio.run(client.generate_reply(review_text, t_voice))
    return {"review_id": review_id, "generated_reply": reply}
