"""Conservative local accounting; provider billing must be independently verified."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text


class BudgetBlocked(RuntimeError):
    pass


def billing_month() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")


def cost_minor(pricing: dict, input_tokens: int, output_tokens: int) -> int:
    value = (
        Decimal(pricing["input_minor_per_million"]) * input_tokens
        + Decimal(pricing["output_minor_per_million"]) * output_tokens
    ) / 1000000
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def price_quote(settings, body: dict) -> tuple[dict, int, int, int]:
    if not settings.model_billing_verified or settings.model_monthly_limit_minor <= 0:
        raise BudgetBlocked("MODEL_BUDGET_NOT_CONFIGURED")
    encoded = json.dumps(body, ensure_ascii=False).encode()
    if len(encoded) > 524288:
        raise BudgetBlocked("MODEL_REQUEST_TOO_LARGE")
    try:
        raw = json.loads(settings.model_prices_json)[body["model"]]
        prices = {}
        for key in ("input_minor_per_million", "output_minor_per_million"):
            rate = Decimal(str(raw[key]))
            if not rate.is_finite() or rate < 0 or rate > 1000000000:
                raise ValueError("invalid price")
            prices[key] = str(rate)
        # UTF-8 bytes plus envelope allowance: a conservative accounting assumption,
        # not a universal theorem about arbitrary providers' tokenizers/billing rules.
        input_bound = len(encoded) + 1024
        output_bound = body.get("max_tokens", 0)
        if type(output_bound) is not int or not 0 <= output_bound <= 900:
            raise ValueError("invalid output bound")
        amount = cost_minor(prices, input_bound, output_bound)
        return prices, input_bound, output_bound, amount
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise BudgetBlocked("MODEL_PRICING_NOT_CONFIGURED") from exc


class ModelBudget:
    def __init__(self, settings, sessions=None, owner_id=None, job_id=None, claim_context=None):
        from app.database import _session_factory

        self.settings = settings
        self.sessions = sessions or _session_factory()
        self.owner_id, self.job_id = owner_id, job_id
        self.claim_context = claim_context

    async def check_claim(self, session):
        if self.job_id is None:
            return  # Explicit system ingestion does not run as a user job.
        claim = self.claim_context or {}
        active = await session.scalar(
            text("""SELECT id FROM jobs WHERE id=:id AND owner_id=:owner
            AND status='RUNNING' AND cancel_requested=false AND lease_until>clock_timestamp()
            AND worker_token=:token AND version=:version FOR SHARE"""),
            {
                "id": self.job_id,
                "owner": self.owner_id,
                "token": claim.get("worker_token"),
                "version": claim.get("version"),
            },
        )
        if active is None:
            raise BudgetBlocked("MODEL_JOB_NO_LONGER_ACTIVE")

    async def reserve(self, endpoint: str, body: dict, effect_key: str | None = None) -> str:
        pricing, input_bound, output_bound, amount = price_quote(self.settings, body)
        period = billing_month()
        call_id, key = str(uuid4()), effect_key or str(uuid4())
        async with self.sessions() as session:
            await self.check_claim(session)
            await session.execute(
                text("""INSERT INTO model_budget_periods(period,limit_minor)
                VALUES (:period,:limit) ON CONFLICT DO NOTHING"""),
                {"period": period, "limit": self.settings.model_monthly_limit_minor},
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM model_budget_periods WHERE period=:period FOR UPDATE"),
                        {"period": period},
                    )
                )
                .mappings()
                .one()
            )
            # Pin the cap for the month; decreasing is allowed only when existing commitments fit.
            limit = min(row["limit_minor"], self.settings.model_monthly_limit_minor)
            if row["halted"] or row["spent_minor"] + row["reserved_minor"] + amount > limit:
                raise BudgetBlocked("MODEL_BUDGET_EXHAUSTED")
            if await session.scalar(
                text("SELECT 1 FROM model_call_reservations WHERE effect_key=:key"), {"key": key}
            ):
                raise BudgetBlocked("MODEL_DISPATCH_ALREADY_RESERVED")
            await session.execute(
                text("""UPDATE model_budget_periods
                SET reserved_minor=reserved_minor+:amount,limit_minor=:limit WHERE period=:period"""),
                {"amount": amount, "limit": limit, "period": period},
            )
            await session.execute(
                text("""INSERT INTO model_call_reservations
                (id,period,effect_key,owner_id,job_id,endpoint,model,pricing,input_token_bound,output_token_bound,reserved_minor,status)
                VALUES (:id,:period,:key,:owner,:job,:endpoint,:model,CAST(:pricing AS jsonb),:input,:output,:amount,'reserved')"""),
                {
                    "id": call_id,
                    "period": period,
                    "key": key,
                    "owner": self.owner_id,
                    "job": self.job_id,
                    "endpoint": endpoint,
                    "model": body["model"],
                    "pricing": json.dumps(pricing),
                    "input": input_bound,
                    "output": output_bound,
                    "amount": amount,
                },
            )
            await session.commit()
        return call_id

    async def dispatch(self, call_id: str):
        async with self.sessions() as session:
            await self.check_claim(session)
            changed = await session.scalar(
                text("""UPDATE model_call_reservations SET status='dispatched'
                WHERE id=:id AND status='reserved' RETURNING id"""),
                {"id": call_id},
            )
            if changed is None:
                raise BudgetBlocked("MODEL_DISPATCH_ALREADY_RESERVED")
            await session.commit()

    async def settle(self, call_id: str, payload: dict | None):
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM model_call_reservations WHERE id=:id FOR UPDATE"),
                        {"id": call_id},
                    )
                )
                .mappings()
                .one()
            )
            if row["status"] != "dispatched":
                return
            amount, status, reported, anomaly = (
                row["reserved_minor"],
                "settled_ceiling",
                None,
                False,
            )
            usage = payload.get("usage") if isinstance(payload, dict) else None
            if isinstance(usage, dict):
                prompt = usage.get(
                    "prompt_tokens",
                    usage.get("total_tokens") if row["output_token_bound"] == 0 else None,
                )
                output = usage.get(
                    "completion_tokens", 0 if row["output_token_bound"] == 0 else None
                )
                if type(prompt) is int and type(output) is int and prompt >= 0 and output >= 0:
                    computed = cost_minor(row["pricing"], prompt, output)
                    reported = computed if computed <= 9223372036854775807 else None
                    anomaly = (
                        prompt > row["input_token_bound"]
                        or output > row["output_token_bound"]
                        or computed > amount
                    )
                    if not anomaly:
                        amount, status = reported, "settled_usage"
                    else:
                        status = "billing_anomaly"
            await session.execute(
                text("""UPDATE model_budget_periods
                SET reserved_minor=reserved_minor-:reserved,spent_minor=spent_minor+:charged,halted=halted OR :halted WHERE period=:period"""),
                {
                    "reserved": row["reserved_minor"],
                    "charged": amount,
                    "halted": anomaly,
                    "period": row["period"],
                },
            )
            await session.execute(
                text("""UPDATE model_call_reservations SET charged_minor=:amount,
                reported_cost_minor=:reported,status=:status,settled_at=now() WHERE id=:id"""),
                {"amount": amount, "reported": reported, "status": status, "id": call_id},
            )
            await session.commit()

    async def own_usage(self, owner_id: str) -> dict:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        text("""SELECT period,count(*) AS calls,
                COALESCE(sum(charged_minor),0) AS accounted_minor,
                COALESCE(sum(CASE WHEN status IN ('reserved','dispatched') THEN reserved_minor ELSE 0 END),0) AS held_minor
                FROM model_call_reservations WHERE owner_id=:owner GROUP BY period ORDER BY period DESC LIMIT 12"""),
                        {"owner": owner_id},
                    )
                )
                .mappings()
                .all()
            )
        return {
            "currency": "CNY",
            "configured": self.settings.model_billing_verified
            and self.settings.model_monthly_limit_minor > 0,
            "periods": [
                {key: int(value) if key != "period" else value for key, value in row.items()}
                for row in rows
            ],
            "notice": "本地配置价格下的保守记账，不是供应商账单。中断的不明费用保留占用，需核账后处理。",
        }
