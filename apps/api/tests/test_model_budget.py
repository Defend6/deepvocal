import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from test_jobs import database_case, submit

from app.config import Settings
from app.jobs import cancel_job
from app.knowledge.budget import BudgetBlocked, ModelBudget, cost_minor, price_quote
from app.knowledge.provider import ModelProvider
from app.worker import claim


def configured(limit=100):
    return Settings(
        model_api_key="test-key",
        model_billing_verified=True,
        model_monthly_limit_minor=limit,
        model_prices_json=json.dumps(
            {"BAAI/bge-m3": {"input_minor_per_million": "1000", "output_minor_per_million": "0"}}
        ),
    )


def test_unconfigured_budget_sends_no_request_and_decimal_rounds_up():
    sent = []
    provider = ModelProvider(
        Settings(model_api_key="test-key"), httpx.MockTransport(lambda r: sent.append(r))
    )
    with pytest.raises(BudgetBlocked, match="NOT_CONFIGURED"):
        asyncio.run(provider.embed(["音准"]))
    assert sent == []
    assert (
        cost_minor({"input_minor_per_million": "0.01", "output_minor_per_million": "1"}, 1, 0) == 1
    )
    with pytest.raises(BudgetBlocked, match="PRICING_NOT_CONFIGURED"):
        price_quote(configured(), {"model": "unpriced-model"})
    with pytest.raises(BudgetBlocked):
        price_quote(configured(), {"model": "BAAI/bge-m3", "input": "x" * 524289})


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_concurrent_budget_reservations_idempotency_settlement_and_owner_isolation(monkeypatch):
    period = "test-" + str(uuid4())
    monkeypatch.setattr("app.knowledge.budget.billing_month", lambda: period)

    async def run():
        async with database_case() as (sessions, owner):
            ledger = ModelBudget(configured(2), sessions, owner)
            body = {"model": "BAAI/bge-m3", "input": ["音准"]}
            calls = await asyncio.gather(
                *[ledger.reserve("/embeddings", body) for _ in range(4)], return_exceptions=True
            )
            successful = [call for call in calls if isinstance(call, str)]
            assert len(successful) == 1
            assert all(
                isinstance(call, BudgetBlocked) for call in calls if not isinstance(call, str)
            )
            call = successful[0]
            await ledger.dispatch(call)
            with pytest.raises(BudgetBlocked):
                await ledger.dispatch(call)
            await ledger.settle(call, {"usage": {"total_tokens": 10}})
            await ledger.settle(call, None)
            own = await ledger.own_usage(owner)
            assert (
                own["periods"][0]["accounted_minor"] == 1 and own["periods"][0]["held_minor"] == 0
            )
            assert (await ledger.own_usage(str(uuid4())))["periods"] == []
            async with sessions() as session:
                row = (
                    (
                        await session.execute(
                            text("SELECT * FROM model_budget_periods WHERE period=:period"),
                            {"period": period},
                        )
                    )
                    .mappings()
                    .one()
                )
                assert row["spent_minor"] == 1 and row["reserved_minor"] == 0
        async with sessions() as session:
            await session.execute(
                text("DELETE FROM model_budget_periods WHERE period=:period"), {"period": period}
            )
            await session.commit()

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_uncertain_cost_is_not_refunded_and_billing_anomaly_halts_calls(monkeypatch):
    period = "test-" + str(uuid4())
    monkeypatch.setattr("app.knowledge.budget.billing_month", lambda: period)

    async def run():
        async with database_case() as (sessions, owner):
            ledger = ModelBudget(configured(100), sessions, owner)
            body = {"model": "BAAI/bge-m3", "input": ["音准"]}
            first = await ledger.reserve("/embeddings", body, "unique-" + period)
            await ledger.dispatch(first)
            await ledger.settle(first, None)
            second = await ledger.reserve("/embeddings", body)
            await ledger.dispatch(second)
            await ledger.settle(second, {"usage": {"total_tokens": 999999}})
            with pytest.raises(BudgetBlocked, match="EXHAUSTED"):
                await ledger.reserve("/embeddings", body)
            async with sessions() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT status,charged_minor,reserved_minor FROM model_call_reservations WHERE owner_id=:owner ORDER BY created_at"
                            ),
                            {"owner": owner},
                        )
                    )
                    .mappings()
                    .all()
                )
                assert (
                    rows[0]["status"] == "settled_ceiling"
                    and rows[0]["charged_minor"] == rows[0]["reserved_minor"]
                )
                assert rows[1]["status"] == "billing_anomaly"
        async with sessions() as session:
            await session.execute(
                text("DELETE FROM model_budget_periods WHERE period=:period"), {"period": period}
            )
            await session.commit()

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_http_accounting_and_cancelled_or_stale_claim_never_sends(monkeypatch):
    period = "test-" + str(uuid4())
    monkeypatch.setattr("app.knowledge.budget.billing_month", lambda: period)
    sent = []

    def response(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"total_tokens": 10},
            },
        )

    async def run():
        async with database_case() as (sessions, owner):
            created = await submit(sessions, owner)
            old = await claim(sessions, created["id"])
            ledger = ModelBudget(configured(), sessions, owner, created["id"], old)
            provider = ModelProvider(configured(), httpx.MockTransport(response), budget=ledger)
            assert await provider.embed(["音准"]) == [[0.1, 0.2]]
            assert len(sent) == 1
            assert (await ledger.own_usage(owner))["periods"][0]["accounted_minor"] == 1
            async with sessions() as session:
                await session.execute(
                    text("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=:id"),
                    {"id": created["id"]},
                )
                await session.commit()
            fresh = await claim(sessions, created["id"])
            with pytest.raises(BudgetBlocked, match="NO_LONGER_ACTIVE"):
                await provider.embed(["过期 claim"])
            fresh_ledger = ModelBudget(configured(), sessions, owner, created["id"], fresh)
            reserved = await fresh_ledger.reserve(
                "/embeddings", {"model": "BAAI/bge-m3", "input": ["音准"]}
            )
            async with sessions() as session:
                await cancel_job(created["id"], {"id": owner}, session)
            with pytest.raises(BudgetBlocked, match="NO_LONGER_ACTIVE"):
                await fresh_ledger.dispatch(reserved)
            fresh_provider = ModelProvider(
                configured(), httpx.MockTransport(response), budget=fresh_ledger
            )
            with pytest.raises(BudgetBlocked, match="NO_LONGER_ACTIVE"):
                await fresh_provider.embed(["已取消"])
            assert len(sent) == 1
        async with sessions() as session:
            await session.execute(
                text("DELETE FROM model_budget_periods WHERE period=:period"), {"period": period}
            )
            await session.commit()

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_http_failure_retains_conservative_charge(monkeypatch):
    period = "test-" + str(uuid4())
    monkeypatch.setattr("app.knowledge.budget.billing_month", lambda: period)

    async def run():
        from app.knowledge.provider import ProviderError

        async with database_case() as (sessions, owner):
            ledger = ModelBudget(configured(), sessions, owner)
            provider = ModelProvider(
                configured(),
                httpx.MockTransport(lambda _: httpx.Response(503, text="secret")),
                budget=ledger,
            )
            with pytest.raises(ProviderError, match="MODEL_REQUEST_FAILED"):
                await provider.embed(["音准"])
            usage = (await ledger.own_usage(owner))["periods"][0]
            assert usage["accounted_minor"] > 0 and usage["held_minor"] == 0
        async with sessions() as session:
            await session.execute(
                text("DELETE FROM model_budget_periods WHERE period=:period"), {"period": period}
            )
            await session.commit()

    asyncio.run(run())
