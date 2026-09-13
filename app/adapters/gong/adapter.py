"""Gong adapter producing canonical conversation documents."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Sequence

import psycopg

from app.adapters.gong.client import GongClient
from app.config import AdapterConfig
from app.contracts import AdapterError
from app.models import (
    ContentUnit,
    Entity,
    NormalizedDocument,
    RecordRef,
    SourceLocator,
)


def _datetime(value: str | None) -> datetime:
    if not value:
        raise ValueError("Gong call is missing its start timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _call_id(payload: dict[str, Any]) -> str:
    metadata = payload.get("metaData") or payload
    return str(metadata.get("id") or metadata.get("callId") or "")


def _fields(values: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        str(row.get("name") or row.get("fieldName")): row.get("value")
        for row in values or []
        if row.get("name") or row.get("fieldName")
    }


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class GongAdapter:
    def __init__(self, config: AdapterConfig) -> None:
        self.id = config.id
        self.config = config

    def configured(self) -> bool:
        return not self.missing_environment()

    def _client(self) -> GongClient:
        return GongClient(
            rate_limit=float(self.config.config.get("rate_limit_per_second", 3))
        )

    def missing_environment(self) -> list[str]:
        return [name for name in self.config.required_env if not os.environ.get(name)]

    def discover(
        self,
        from_datetime: datetime | None,
        to_datetime: datetime | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[RecordRef]:
        del checkpoint
        if from_datetime is None or to_datetime is None:
            raise ValueError("Gong discovery requires a date window")
        client = self._client()
        refs: list[RecordRef] = []
        seen: set[str] = set()
        for basic in client.list_calls(from_datetime, to_datetime):
            external_id = _call_id(basic)
            if external_id and external_id not in seen:
                refs.append(
                    RecordRef(
                        external_id=external_id,
                        locator=SourceLocator({"basic": basic}),
                        updated_at=_datetime(basic["updated"])
                        if basic.get("updated")
                        else None,
                    )
                )
                seen.add(external_id)
        return refs

    def fetch_batch(self, refs: Sequence[RecordRef]) -> list[dict[str, Any]]:
        ids = [ref.external_id for ref in refs]
        if len(ids) > 100:
            raise ValueError("Gong batches cannot exceed 100 calls")
        client = self._client()
        extensive = {call_id: row for row in client.extensive(ids) if (call_id := _call_id(row))}
        missing = [call_id for call_id in ids if call_id not in extensive]
        if missing:
            raise AdapterError(f"Gong omitted extensive calls: {missing}")
        transcripts = {
            str(row.get("callId")): row
            for row in client.transcripts(ids)
            if row.get("callId") is not None
        }
        return [
            {
                "basic": ref.locator.value.get("basic"),
                "extensive": extensive[ref.external_id],
                "transcript": transcripts.get(ref.external_id),
            }
            for ref in refs
        ]

    def normalize(self, raw: dict[str, Any]) -> NormalizedDocument:
        basic = raw.get("basic") or {}
        extensive = raw["extensive"]
        metadata = {**basic, **(extensive.get("metaData") or {})}
        call_id = _call_id(extensive)
        if not call_id:
            raise ValueError("Gong extensive call is missing id")
        parties = extensive.get("parties") or []
        speaker_names = {
            str(party.get("speakerId")): party.get("name")
            or party.get("emailAddress")
            or str(party.get("speakerId"))
            for party in parties
            if party.get("speakerId") is not None
        }

        units: list[ContentUnit] = []
        transcript = raw.get("transcript") or {}
        for segment in transcript.get("transcript") or []:
            speaker_id = str(segment.get("speakerId") or "")
            for sentence in segment.get("sentences") or []:
                text = str(sentence.get("text") or "").strip()
                if not text:
                    continue
                sequence = len(units) + 1
                units.append(
                    ContentUnit(
                        sequence=sequence,
                        text=text,
                        author=speaker_names.get(speaker_id) or speaker_id or None,
                        started_at_ms=int(sentence["start"])
                        if sentence.get("start") is not None
                        else None,
                        ended_at_ms=int(sentence["end"])
                        if sentence.get("end") is not None
                        else None,
                        metadata={"topic": segment.get("topic")},
                        locator=SourceLocator(
                            {
                                "call_id": call_id,
                                "speaker_id": speaker_id or None,
                                "sentence": sequence,
                                "start_ms": sentence.get("start"),
                                "end_ms": sentence.get("end"),
                            }
                        ),
                    )
                )

        context = extensive.get("context") or []
        crm_objects = [
            obj
            for context_group in context
            for obj in context_group.get("objects") or []
        ]
        entities = [
            Entity(
                type="person",
                external_id=str(party.get("id")) if party.get("id") else None,
                name=party.get("name"),
                metadata={
                    "email": party.get("emailAddress") or party.get("email"),
                    "affiliation": party.get("affiliation"),
                    "title": party.get("title"),
                },
            )
            for party in parties
        ]
        entities.extend(
            Entity(
                type=str(obj.get("objectType") or "crm_object").lower(),
                external_id=str(obj.get("objectId")) if obj.get("objectId") else None,
                name=_fields(obj.get("fields")).get("Name"),
                metadata=_fields(obj.get("fields")),
            )
            for obj in crm_objects
        )
        content = extensive.get("content") or {}
        normalized_metadata = {
            "workspace_id": metadata.get("workspaceId"),
            "primary_user_id": metadata.get("primaryUserId"),
            "scheduled_at": metadata.get("scheduled") or metadata.get("scheduledAt"),
            "duration_sec": metadata.get("duration"),
            "direction": metadata.get("direction"),
            "scope": metadata.get("scope"),
            "media_type": metadata.get("media") or metadata.get("mediaType"),
            "language": metadata.get("language"),
            "system": metadata.get("system"),
            "transcript_status": "complete" if units else "pending",
            "topics": content.get("topics") or [],
            "trackers": content.get("trackers") or [],
            "crm": crm_objects,
            "participants": parties,
        }
        return NormalizedDocument(
            source_id=self.id,
            external_id=call_id,
            kind="conversation",
            title=metadata.get("title"),
            occurred_at=_datetime(metadata.get("started") or metadata.get("startedAt")),
            updated_at=_datetime(metadata["updated"])
            if metadata.get("updated")
            else None,
            url=metadata.get("url"),
            metadata=normalized_metadata,
            units=tuple(units),
            entities=tuple(entities),
            raw=raw,
        )

    def persist_projection(
        self,
        conn: psycopg.Connection,
        document_id: str,
        raw: dict[str, Any],
        document: NormalizedDocument,
    ) -> None:
        metadata = document.metadata
        parties = metadata["participants"]
        for party in parties:
            user_id = party.get("userId")
            if not user_id:
                continue
            names = str(party.get("name") or "").split(" ", 1)
            conn.execute(
                """
                INSERT INTO gong_users (
                  source_id, external_id, email, first_name, last_name,
                  title, active, raw
                ) VALUES (%s, %s, %s, %s, %s, %s, true, %s)
                ON CONFLICT (source_id, external_id) DO UPDATE SET
                  email = EXCLUDED.email,
                  first_name = EXCLUDED.first_name,
                  last_name = EXCLUDED.last_name,
                  title = EXCLUDED.title,
                  raw = EXCLUDED.raw,
                  updated_at = now()
                """,
                (
                    self.id,
                    str(user_id),
                    party.get("emailAddress") or party.get("email"),
                    names[0] if names else None,
                    names[1] if len(names) > 1 else None,
                    party.get("title"),
                    json.dumps(party),
                ),
            )
        conn.execute(
            """
            INSERT INTO gong_calls (
              document_id, workspace_id, primary_user_external_id, scheduled_at,
              duration_sec, direction, scope, media_type, language, system,
              participant_count, external_participant_count
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE SET
              workspace_id = EXCLUDED.workspace_id,
              primary_user_external_id = EXCLUDED.primary_user_external_id,
              scheduled_at = EXCLUDED.scheduled_at,
              duration_sec = EXCLUDED.duration_sec,
              direction = EXCLUDED.direction,
              scope = EXCLUDED.scope,
              media_type = EXCLUDED.media_type,
              language = EXCLUDED.language,
              system = EXCLUDED.system,
              participant_count = EXCLUDED.participant_count,
              external_participant_count = EXCLUDED.external_participant_count
            """,
            (
                document_id,
                metadata.get("workspace_id"),
                metadata.get("primary_user_id"),
                metadata.get("scheduled_at"),
                metadata.get("duration_sec"),
                metadata.get("direction"),
                metadata.get("scope"),
                metadata.get("media_type"),
                metadata.get("language"),
                metadata.get("system"),
                len(parties),
                sum(1 for party in parties if party.get("affiliation") == "External"),
            ),
        )
        for table in (
            "gong_participants",
            "gong_crm_associations",
            "gong_topics",
            "gong_tracker_hits",
        ):
            conn.execute(f"DELETE FROM {table} WHERE document_id = %s", (document_id,))
        for party in parties:
            party_id = party.get("id") or party.get("partyId")
            if not party_id:
                continue
            conn.execute(
                """
                INSERT INTO gong_participants (
                  document_id, party_external_id, user_external_id,
                  speaker_external_id, email, name, title, affiliation,
                  phone_number
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    document_id,
                    str(party_id),
                    party.get("userId"),
                    party.get("speakerId"),
                    party.get("emailAddress") or party.get("email"),
                    party.get("name"),
                    party.get("title"),
                    party.get("affiliation"),
                    party.get("phoneNumber"),
                ),
            )
        for obj in metadata["crm"]:
            fields = _fields(obj.get("fields"))
            conn.execute(
                """
                INSERT INTO gong_crm_associations (
                  document_id, object_type, object_external_id, account_name,
                  opportunity_name, deal_stage, deal_amount, fields
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    document_id,
                    obj.get("objectType") or "Unknown",
                    obj.get("objectId"),
                    fields.get("AccountName")
                    or (fields.get("Name") if obj.get("objectType") == "Account" else None),
                    fields.get("Name")
                    if obj.get("objectType") == "Opportunity"
                    else None,
                    fields.get("StageName"),
                    _number(fields.get("Amount")),
                    json.dumps(fields),
                ),
            )
        for topic in metadata["topics"]:
            name = topic.get("name") or topic.get("topic")
            if name:
                conn.execute(
                    "INSERT INTO gong_topics VALUES (%s, %s, %s)",
                    (document_id, name, topic.get("duration")),
                )
        for tracker in metadata["trackers"]:
            name = tracker.get("name") or tracker.get("trackerName")
            if name:
                conn.execute(
                    """
                    INSERT INTO gong_tracker_hits (
                      document_id, tracker_external_id, name, hit_count, occurrences
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        tracker.get("id"),
                        name,
                        tracker.get("count") or tracker.get("hitCount") or 0,
                        json.dumps(tracker.get("occurrences") or []),
                    ),
                )
