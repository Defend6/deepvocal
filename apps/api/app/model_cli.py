"""Read-only local accounting/preflight. Never prints API keys or prompt content."""

import argparse
import asyncio
import json

from sqlalchemy import text

from app.config import get_settings
from app.database import _session_factory
from app.knowledge.budget import BudgetBlocked, billing_month, price_quote


async def status():
    async with _session_factory()() as session:
        period = (
            (
                await session.execute(
                    text("SELECT * FROM model_budget_periods WHERE period=:period"),
                    {"period": billing_month()},
                )
            )
            .mappings()
            .first()
        )
        unsettled = (
            (
                await session.execute(
                    text("""SELECT id,period,endpoint,model,status,reserved_minor,created_at
            FROM model_call_reservations WHERE status IN ('reserved','dispatched','billing_anomaly') ORDER BY created_at LIMIT 100""")
                )
            )
            .mappings()
            .all()
        )
    return {
        "period": dict(period) if period else None,
        "needs_reconciliation": [dict(row) for row in unsettled],
        "notice": "只读状态；不明费用不自动释放。核对供应商账单与进程后再人工处理。",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "status"])
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps(asyncio.run(status()), ensure_ascii=False, default=str, indent=2))
        return
    settings = get_settings()
    checks = {"key_present": bool(settings.model_api_key)}
    for model in (settings.chat_model, settings.embedding_model, settings.reranker_model):
        try:
            price_quote(settings, {"model": model})
        except BudgetBlocked as exc:
            checks[model] = str(exc)
        else:
            checks[model] = "configured"
    print(json.dumps({"checks": checks, "network_requests": 0}, ensure_ascii=False, indent=2))
    raise SystemExit(
        0
        if checks["key_present"]
        and all(value == "configured" for key, value in checks.items() if key != "key_present")
        else 1
    )


if __name__ == "__main__":
    main()
