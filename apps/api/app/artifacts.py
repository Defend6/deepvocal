from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.identity import Session, User
from app.planning import PlanEdit, PracticeCreate

router = APIRouter(prefix="/v1", tags=["reports and practice"])
TABLES = {"reports": "assessment_reports", "plans": "practice_plans"}


async def materialize(session, job: dict, result: dict) -> dict:
    """Called in the fenced job finish transaction; cancelled attempts cannot publish artifacts."""
    kind = job["kind"]
    if kind == "assessment" and result.get("status") == "measured":
        table, key, payload = "assessment_reports", "report_id", result
    elif kind == "planning" and result.get("status") == "planned":
        table, key, payload = "practice_plans", "plan_id", result["plan"]
    else:
        return result
    artifact_id = await session.scalar(
        text(f"""
        INSERT INTO {table}(id,owner_id,source_job_id,payload)
        VALUES (:id,:owner,:job,CAST(:payload AS jsonb))
        ON CONFLICT(source_job_id) DO NOTHING RETURNING id
    """),
        {
            "id": str(uuid4()),
            "owner": str(job["owner_id"]),
            "job": str(job["id"]),
            "payload": json.dumps(payload, allow_nan=False),
        },
    )
    if artifact_id is None:
        artifact_id = await session.scalar(
            text(f"SELECT id FROM {table} WHERE source_job_id=:job AND owner_id=:owner"),
            {"job": str(job["id"]), "owner": str(job["owner_id"])},
        )
    result = {**result, key: str(artifact_id)}
    if kind == "assessment" and result.get("followup", {}).get("status") == "planned":
        plan = {**result["followup"]["plan"], "report_id": str(artifact_id)}
        followup = await materialize(
            session, {**job, "kind": "planning"}, {"status": "planned", "plan": plan}
        )
        result["plan_id"] = followup["plan_id"]
    return result


async def owned(session, table: str, artifact_id: UUID, owner: str, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    row = (
        (
            await session.execute(
                text(f"SELECT * FROM {table} WHERE id=:id AND owner_id=:owner{suffix}"),
                {"id": str(artifact_id), "owner": owner},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "artifact not found")
    return row


@router.get("/reports")
async def reports(user: User, session: Session):
    return await list_artifacts(session, "assessment_reports", user["id"])


@router.get("/plans")
async def plans(user: User, session: Session):
    return await list_artifacts(session, "practice_plans", user["id"])


async def list_artifacts(session, table, owner):
    rows = (
        await session.execute(
            text(
                f"SELECT * FROM {table} WHERE owner_id=:owner ORDER BY created_at DESC,id DESC LIMIT 100"
            ),
            {"owner": owner},
        )
    ).mappings()
    return [jsonable_encoder(dict(row)) for row in rows]


@router.get("/reports/{artifact_id}")
async def report(artifact_id: UUID, user: User, session: Session):
    return jsonable_encoder(
        dict(await owned(session, "assessment_reports", artifact_id, user["id"]))
    )


@router.get("/plans/{artifact_id}")
async def plan(artifact_id: UUID, user: User, session: Session):
    return jsonable_encoder(dict(await owned(session, "practice_plans", artifact_id, user["id"])))


@router.patch("/plans/{artifact_id}")
async def edit_plan(artifact_id: UUID, request: PlanEdit, user: User, session: Session):
    row = await owned(session, "practice_plans", artifact_id, user["id"], lock=True)
    if row["version"] != request.version:
        raise HTTPException(409, "plan changed; reload before editing")
    payload = {
        **row["payload"],
        **request.model_dump(exclude={"version"}),
        "minutes": sum(item.seconds for item in request.exercises) / 60,
    }
    updated = (
        (
            await session.execute(
                text("""UPDATE practice_plans SET payload=CAST(:payload AS jsonb),
        version=version+1,updated_at=now() WHERE id=:id AND owner_id=:owner RETURNING *"""),
                {"payload": json.dumps(payload), "id": str(artifact_id), "owner": user["id"]},
            )
        )
        .mappings()
        .one()
    )
    await session.commit()
    return jsonable_encoder(dict(updated))


@router.post("/plans/{artifact_id}/practice", status_code=201)
async def record_practice(artifact_id: UUID, request: PracticeCreate, user: User, session: Session):
    row = await owned(session, "practice_plans", artifact_id, user["id"], lock=True)
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    digest = hashlib.sha256(
        json.dumps({"plan": str(artifact_id), **payload}, sort_keys=True).encode()
    ).hexdigest()
    existing = (
        (
            await session.execute(
                text(
                    "SELECT * FROM practice_records WHERE owner_id=:owner AND idempotency_key=:key"
                ),
                {"owner": user["id"], "key": str(request.idempotency_key)},
            )
        )
        .mappings()
        .first()
    )
    if existing:
        if existing["request_hash"] != digest:
            raise HTTPException(409, "practice key reused with different input")
        return jsonable_encoder(dict(existing))
    if row["version"] != request.plan_version:
        raise HTTPException(409, "plan version changed; reload before recording")
    result = (
        (
            await session.execute(
                text("""INSERT INTO practice_records
        (id,owner_id,plan_id,idempotency_key,request_hash,plan_version,payload)
        VALUES (:id,:owner,:plan,:key,:hash,:version,CAST(:payload AS jsonb))
        ON CONFLICT(owner_id,idempotency_key) DO NOTHING RETURNING *"""),
                {
                    "id": str(uuid4()),
                    "owner": user["id"],
                    "plan": str(artifact_id),
                    "key": str(request.idempotency_key),
                    "hash": digest,
                    "version": request.plan_version,
                    "payload": json.dumps(payload),
                },
            )
        )
        .mappings()
        .first()
    )
    if result is None:
        result = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM practice_records WHERE owner_id=:owner AND idempotency_key=:key"
                    ),
                    {"owner": user["id"], "key": str(request.idempotency_key)},
                )
            )
            .mappings()
            .one()
        )
        if result["request_hash"] != digest:
            raise HTTPException(409, "practice key reused with different input")
    await session.commit()
    return jsonable_encoder(dict(result))


@router.get("/practice")
async def practice(user: User, session: Session):
    return await list_artifacts(session, "practice_records", user["id"])


@router.post("/plans/{artifact_id}/adapt")
async def adapt_plan(artifact_id: UUID, user: User, session: Session):
    from app.planning import PlanningInput, create_plan

    row = await owned(session, "practice_plans", artifact_id, user["id"], lock=True)
    history = (
        (
            await session.execute(
                text("""SELECT payload FROM practice_records
      WHERE owner_id=:owner AND plan_id=:plan AND plan_version=:version
      ORDER BY created_at DESC,id DESC LIMIT 3"""),
                {"owner": user["id"], "plan": str(artifact_id), "version": row["version"]},
            )
        )
        .mappings()
        .all()
    )
    if not history:
        raise HTTPException(409, "record feedback for this plan version before adapting")
    report_id = row["payload"].get("report_id")
    report = (
        await owned(session, "assessment_reports", UUID(report_id), user["id"])
        if report_id
        else None
    )
    result = create_plan(
        PlanningInput(
            goal=row["payload"]["goal"],
            minutes=max(5, min(30, int(row["payload"]["minutes"]))),
            days=row["payload"]["days"],
            report_id=report_id,
        ),
        report["payload"] if report else None,
        [dict(h) for h in history],
    )
    if result["status"] != "planned":
        return result
    await session.execute(
        text("""UPDATE practice_plans SET payload=CAST(:payload AS jsonb),
      version=version+1,updated_at=now() WHERE id=:id AND owner_id=:owner"""),
        {"payload": json.dumps(result["plan"]), "id": str(artifact_id), "owner": user["id"]},
    )
    from app.security import audit

    await audit(session, user["id"], "plan.adapted", str(artifact_id))
    await session.commit()
    return await plan(artifact_id, user, session)


@router.get("/progress")
async def progress(user: User, session: Session):
    records = await list_artifacts(session, "practice_records", user["id"])
    reports = await list_artifacts(session, "assessment_reports", user["id"])
    return {
        "practice_count": len(records),
        "practice_seconds": sum(r["payload"].get("duration_seconds", 0) for r in records),
        "reports": [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "profile": r["payload"].get("profile"),
                "algorithm": r["payload"].get("algorithm_version"),
                "ruleset": r["payload"].get("ruleset_version"),
                "reference_fingerprint": r["payload"].get("reference_fingerprint"),
                "quality": r["payload"].get("quality"),
                "total": r["payload"].get("total"),
            }
            for r in reversed(reports)
        ],
        "scope": "most recent 100 reports and practice records",
    }
