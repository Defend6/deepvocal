"""Private reference melody catalog with explicit provenance and bounded MIDI import."""

import base64
import io
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from app.audio import AssessmentInput, ReferenceNote
from app.identity import Session, User

router = APIRouter(prefix="/v1/songs", tags=["reference melodies"])


class SongCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    notes: list[ReferenceNote] = Field(default_factory=list, max_length=3000)
    midi_base64: str | None = Field(default=None, max_length=2800000)
    track: int | None = Field(default=None, ge=0, le=255)
    license: Literal["original", "CC0", "CC-BY", "permission"]
    attribution: str = Field(min_length=1, max_length=1000)
    rights_confirmed: Literal[True]

    @model_validator(mode="after")
    def melody_required(self):
        if bool(self.notes) == bool(self.midi_base64):
            raise ValueError("provide notes or one MIDI file")
        return self


def midi_notes(encoded, track=None):
    import mido

    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("MIDI_SIZE_LIMIT")
        midi = mido.MidiFile(file=io.BytesIO(data))
        if midi.type == 2 or len(midi.tracks) > 256:
            raise ValueError("MIDI_TYPE_UNSUPPORTED")
        if track is not None:
            if track >= len(midi.tracks):
                raise ValueError("MIDI_TRACK_NOT_FOUND")
            # Keep tempo events from all tracks so a selected melody retains its timeline.
            for index, messages in enumerate(midi.tracks):
                if index != track:
                    elapsed = 0
                    filtered = mido.MidiTrack()
                    for message in messages:
                        elapsed += message.time
                        if message.is_meta:
                            filtered.append(message.copy(time=elapsed))
                            elapsed = 0
                    midi.tracks[index] = filtered
        clock, opened, notes, events = 0.0, {}, [], 0
        for message in midi:
            clock += message.time
            events += 1
            if clock > 600 or events > 100000:
                raise ValueError("MIDI_DURATION_OR_EVENT_LIMIT")
            if message.is_meta or getattr(message, "channel", -1) == 9:
                continue
            if message.type == "note_on" and message.velocity > 0:
                if opened:
                    raise ValueError("MIDI_MELODY_MUST_BE_MONOPHONIC")
                opened[(message.channel, message.note)] = clock
            elif message.type == "note_off" or (
                message.type == "note_on" and message.velocity == 0
            ):
                start = opened.pop((message.channel, message.note), None)
                if start is not None:
                    notes.append(
                        ReferenceNote(
                            start_ms=round(start * 1000),
                            end_ms=round(clock * 1000),
                            midi=message.note,
                        )
                    )
        if opened or not notes:
            raise ValueError("MIDI_INCOMPLETE_MELODY")
        AssessmentInput(profile="reference_song", reference_notes=notes)
        return notes
    except (ValueError, OSError, EOFError, KeyError) as exc:
        raise HTTPException(
            422, "无法导入旋律，请提供不超过10分钟的单声部 MIDI，并选择旋律轨道。"
        ) from exc


@router.post("", status_code=201)
async def create_song(body: SongCreate, user: User, session: Session):
    notes = midi_notes(body.midi_base64, body.track) if body.midi_base64 else body.notes
    try:
        AssessmentInput(profile="reference_song", reference_notes=notes)
    except ValueError as exc:
        raise HTTPException(422, "参考旋律的音符、时长或排列不符合要求。") from exc
    identifier = str(uuid4())
    payload = {
        "notes": [n.model_dump() for n in notes],
        "license": body.license,
        "attribution": body.attribution,
        "rights_confirmed": body.rights_confirmed,
    }
    await session.execute(
        text("""INSERT INTO song_references(id,owner_id,title,payload)
      VALUES (:id,:owner,:title,CAST(:payload AS jsonb))"""),
        {
            "id": identifier,
            "owner": user["id"],
            "title": body.title,
            "payload": json.dumps(payload),
        },
    )
    await session.commit()
    return {"id": identifier, "title": body.title, "version": 1, "payload": payload}


@router.get("")
async def songs(user: User, session: Session):
    rows = (
        await session.execute(
            text("""SELECT * FROM song_references WHERE owner_id=:owner
      ORDER BY created_at DESC LIMIT 100"""),
            {"owner": user["id"]},
        )
    ).mappings()
    return jsonable_encoder([dict(r) for r in rows])


@router.delete("/{song_id}", status_code=204)
async def delete_song(song_id: UUID, user: User, session: Session):
    result = await session.execute(
        text("DELETE FROM song_references WHERE id=:id AND owner_id=:owner"),
        {"id": str(song_id), "owner": user["id"]},
    )
    if not result.rowcount:
        raise HTTPException(404, "reference not found")
    await session.commit()
