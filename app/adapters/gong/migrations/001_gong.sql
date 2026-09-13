CREATE TABLE gong_users (
  source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  external_id     TEXT NOT NULL,
  email           TEXT,
  first_name      TEXT,
  last_name       TEXT,
  title           TEXT,
  manager_external_id TEXT,
  active          BOOLEAN NOT NULL DEFAULT true,
  raw             JSONB,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, external_id)
);

CREATE INDEX gong_users_email ON gong_users (lower(email));

CREATE TABLE gong_calls (
  document_id       UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  workspace_id      TEXT,
  primary_user_external_id TEXT,
  scheduled_at      TIMESTAMPTZ,
  duration_sec      INT,
  direction         TEXT,
  scope             TEXT,
  media_type        TEXT,
  language          TEXT,
  system            TEXT,
  participant_count INT NOT NULL DEFAULT 0,
  external_participant_count INT NOT NULL DEFAULT 0
);

CREATE INDEX gong_calls_scope ON gong_calls (scope);
CREATE INDEX gong_calls_primary_user ON gong_calls (primary_user_external_id);

CREATE TABLE gong_participants (
  id                BIGSERIAL PRIMARY KEY,
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  party_external_id TEXT NOT NULL,
  user_external_id  TEXT,
  speaker_external_id TEXT,
  email             TEXT,
  name              TEXT,
  title             TEXT,
  affiliation       TEXT,
  phone_number      TEXT,
  talk_time_sec     NUMERIC,
  UNIQUE (document_id, party_external_id)
);

CREATE INDEX gong_participants_document ON gong_participants (document_id);
CREATE INDEX gong_participants_email ON gong_participants (lower(email));

CREATE TABLE gong_crm_associations (
  id                BIGSERIAL PRIMARY KEY,
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  object_type       TEXT NOT NULL,
  object_external_id TEXT,
  account_name      TEXT,
  opportunity_name  TEXT,
  deal_stage        TEXT,
  deal_amount       NUMERIC,
  fields            JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX gong_crm_document ON gong_crm_associations (document_id);
CREATE INDEX gong_crm_account ON gong_crm_associations (lower(account_name));
CREATE INDEX gong_crm_stage ON gong_crm_associations (lower(deal_stage));

CREATE TABLE gong_topics (
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  duration_sec      NUMERIC,
  PRIMARY KEY (document_id, name)
);

CREATE TABLE gong_tracker_hits (
  id                BIGSERIAL PRIMARY KEY,
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tracker_external_id TEXT,
  name              TEXT NOT NULL,
  hit_count         INT NOT NULL DEFAULT 0,
  occurrences       JSONB NOT NULL DEFAULT '[]'
);

CREATE VIEW gong_call_context AS
SELECT
  dc.*,
  gc.workspace_id,
  gc.primary_user_external_id,
  gu.email AS primary_user_email,
  concat_ws(' ', gu.first_name, gu.last_name) AS primary_user_name,
  gc.scheduled_at,
  gc.duration_sec,
  gc.direction,
  gc.scope,
  gc.media_type,
  gc.language,
  gc.system,
  gc.participant_count,
  gc.external_participant_count,
  (
    SELECT jsonb_agg(to_jsonb(p) - 'document_id' ORDER BY p.id)
    FROM gong_participants p
    WHERE p.document_id = dc.id
  ) AS participants,
  (
    SELECT jsonb_agg(to_jsonb(crm) - 'document_id' ORDER BY crm.id)
    FROM gong_crm_associations crm
    WHERE crm.document_id = dc.id
  ) AS crm,
  (
    SELECT jsonb_agg(to_jsonb(topic) - 'document_id' ORDER BY topic.name)
    FROM gong_topics topic
    WHERE topic.document_id = dc.id
  ) AS topics,
  (
    SELECT jsonb_agg(to_jsonb(tracker) - 'document_id' ORDER BY tracker.id)
    FROM gong_tracker_hits tracker
    WHERE tracker.document_id = dc.id
  ) AS trackers
FROM document_context dc
JOIN gong_calls gc ON gc.document_id = dc.id
LEFT JOIN gong_users gu
  ON gu.source_id = dc.source_id
 AND gu.external_id = gc.primary_user_external_id;

COMMENT ON TABLE gong_calls IS
  'Gong-specific projection linked to a canonical document.';
COMMENT ON VIEW gong_call_context IS
  'Rich Gong call projection for optional adapter-specific MCP tools.';
