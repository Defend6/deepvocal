from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, authenticate
from app.database import get_session


async def current_user(
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    result = await session.execute(
        text("""
            INSERT INTO users (id, issuer, subject)
            VALUES (gen_random_uuid(), :issuer, :subject)
            ON CONFLICT (issuer, subject) DO UPDATE SET issuer = EXCLUDED.issuer
            RETURNING id, issuer, subject, status
        """),
        {"issuer": principal.issuer, "subject": principal.subject},
    )
    user = result.mappings().one()
    if user["status"] != "active":
        await session.rollback()
        raise HTTPException(status_code=403, detail="user account is not active")
    await session.commit()
    return {"id": str(user["id"]), "issuer": user["issuer"], "subject": user["subject"]}


User = Annotated[dict[str, str], Depends(current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]
