"""Billing lifecycle tasks: trial expiry, auto-suspension and recurring charges."""

import logging

from reput_ai.db.session import async_session_maker
from reput_ai.services.billing import BillingService
from reput_ai.workers.celery_app import celery_app
from reput_ai.workers.runtime import run_async

logger = logging.getLogger("reput_ai.workers.billing")


@celery_app.task(name="reput_ai.workers.tasks.billing_tasks.expire_trial_subscriptions")
def expire_trial_subscriptions() -> dict[str, object]:
    """TRIAL -> PAST_DUE for expired trials (branches are suspended automatically)."""
    expired = run_async(BillingService().expire_trials(async_session_maker))
    return {"status": "success", "expired": expired}


@celery_app.task(name="reput_ai.workers.tasks.billing_tasks.charge_due_subscriptions")
def charge_due_subscriptions() -> dict[str, object]:
    """Recurring auto-renewal of ACTIVE subscriptions using the saved payment method."""
    charged = run_async(BillingService().charge_due_subscriptions(async_session_maker))
    return {"status": "success", "charged": charged}


@celery_app.task(name="reput_ai.workers.tasks.billing_tasks.suspend_delinquent_subscriptions")
def suspend_delinquent_subscriptions() -> dict[str, object]:
    """PAST_DUE -> CANCELED once the grace period after the failed payment is over."""
    canceled = run_async(BillingService().suspend_delinquent_subscriptions(async_session_maker))
    return {"status": "success", "canceled": canceled}


@celery_app.task(name="reput_ai.workers.tasks.billing_tasks.run_billing_lifecycle")
def run_billing_lifecycle() -> dict[str, object]:
    """Hourly sweep: expire trials, renew subscriptions, cancel unrecovered debtors."""
    billing = BillingService()
    expired = run_async(billing.expire_trials(async_session_maker))
    charged = run_async(billing.charge_due_subscriptions(async_session_maker))
    canceled = run_async(billing.suspend_delinquent_subscriptions(async_session_maker))

    logger.info(
        "Billing lifecycle finished: %s trial(s) expired, %s charge(s) queued, %s canceled",
        expired,
        charged,
        canceled,
    )
    return {
        "status": "success",
        "expired_trials": expired,
        "charged": charged,
        "canceled": canceled,
    }
