-- DeepVocal V1.4 initial design schema. PostgreSQL. NOT an executed migration.
-- UUIDs and owner context must be supplied by the trusted service.
BEGIN;
CREATE TABLE users (
 id uuid PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL, timezone text NOT NULL DEFAULT 'Asia/Shanghai',
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','pending_delete')),
 created_at timestamptz NOT NULL DEFAULT now(), data_revision bigint NOT NULL DEFAULT 1, memory_revision bigint NOT NULL DEFAULT 1, UNIQUE(issuer,subject)
);
CREATE TABLE user_profiles (
 user_id uuid PRIMARY KEY REFERENCES users(id), goals jsonb NOT NULL DEFAULT '[]',
 experience text, minutes_per_day integer NOT NULL DEFAULT 15 CHECK(minutes_per_day BETWEEN 5 AND 120),
 updated_at timestamptz NOT NULL DEFAULT now(), version integer NOT NULL DEFAULT 1 CHECK(version>=1)
);
CREATE TABLE sessions (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), title text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id)
);
CREATE TABLE messages (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), session_id uuid NOT NULL,
 role text NOT NULL CHECK(role IN ('user','assistant','tool')), content jsonb NOT NULL,
 trace_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id),
 FOREIGN KEY(session_id,owner_id) REFERENCES sessions(id,owner_id)
);
CREATE INDEX messages_session_time ON messages(session_id,created_at,id);
CREATE TABLE audio_assets (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), object_key text NOT NULL UNIQUE,
 sha256 char(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'), content_type text NOT NULL,
 size_bytes bigint NOT NULL CHECK(size_bytes>0 AND size_bytes<=104857600),
 duration_ms bigint CHECK(duration_ms>0 AND duration_ms<=600000),
 purpose text NOT NULL CHECK(purpose IN ('assessment','voice_sample','melody','reference','generated_reference')),
 object_version text, verified_at timestamptz, expires_at timestamptz,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','invalid','pending_delete','deleted')),
 created_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz, UNIQUE(id,owner_id)
);
CREATE INDEX assets_owner_time ON audio_assets(owner_id,created_at DESC);
CREATE TABLE voice_consents (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 scope text NOT NULL CHECK(scope='singing_reference'), text_version text NOT NULL,
 declaration jsonb NOT NULL, granted_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
 CHECK(revoked_at IS NULL OR revoked_at>=granted_at), UNIQUE(id,owner_id)
);
CREATE TABLE jobs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 kind text NOT NULL CHECK(kind IN ('assessment','singing','ingestion','memory','deletion','evaluation')),
 status text NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
 input_asset_id uuid, request jsonb NOT NULL, result jsonb, error_code text,
 attempt integer NOT NULL DEFAULT 0 CHECK(attempt>=0), version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 cancel_requested boolean NOT NULL DEFAULT false, lease_until timestamptz, claim_token uuid, provider_request_id text,
 trace_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id), FOREIGN KEY(input_asset_id,owner_id) REFERENCES audio_assets(id,owner_id)
);
CREATE INDEX jobs_owner_time ON jobs(owner_id,created_at DESC);
CREATE INDEX jobs_ready ON jobs(status,lease_until,created_at);
CREATE TABLE assessment_reports (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), job_id uuid NOT NULL UNIQUE,
 reference_asset_id uuid, quality jsonb NOT NULL CHECK(jsonb_typeof(quality)='object'), metrics jsonb NOT NULL CHECK(jsonb_typeof(metrics)='array'),
 scores jsonb NOT NULL CHECK(jsonb_typeof(scores)='object'), measurement_version text NOT NULL,
 reference_snapshot jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(reference_snapshot)='object'), expires_at timestamptz,
 algorithm_version text NOT NULL, explanation_version text NOT NULL, limitations jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id),
 FOREIGN KEY(job_id,owner_id) REFERENCES jobs(id,owner_id),
 FOREIGN KEY(reference_asset_id,owner_id) REFERENCES audio_assets(id,owner_id)
);
CREATE TABLE report_segments (
 id uuid PRIMARY KEY, report_id uuid NOT NULL REFERENCES assessment_reports(id),
 start_ms bigint NOT NULL CHECK(start_ms>=0), end_ms bigint NOT NULL,
 measurements jsonb NOT NULL, recommendations jsonb NOT NULL DEFAULT '[]', CHECK(end_ms>start_ms)
);
CREATE INDEX report_segments_time ON report_segments(report_id,start_ms);
CREATE TABLE practice_plans (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), report_id uuid,
 days integer NOT NULL CHECK(days IN (7,14)), minutes_per_day integer NOT NULL CHECK(minutes_per_day BETWEEN 5 AND 120),
 family_id uuid NOT NULL, revision integer NOT NULL DEFAULT 1 CHECK(revision>=1),
 status text NOT NULL CHECK(status IN ('active','completed','archived')), version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id),
 FOREIGN KEY(report_id,owner_id) REFERENCES assessment_reports(id,owner_id)
);
CREATE TABLE practice_items (
 id uuid PRIMARY KEY, plan_id uuid NOT NULL, owner_id uuid NOT NULL REFERENCES users(id),
 day_index integer NOT NULL CHECK(day_index BETWEEN 1 AND 14), duration_minutes integer NOT NULL CHECK(duration_minutes BETWEEN 1 AND 120),
 exercise_code text NOT NULL, instructions text NOT NULL, stop_rule text NOT NULL,
 source_refs jsonb NOT NULL DEFAULT '[]', UNIQUE(id,owner_id),
 FOREIGN KEY(plan_id,owner_id) REFERENCES practice_plans(id,owner_id)
);
CREATE TABLE practice_logs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), item_id uuid NOT NULL,
 completed_at timestamptz NOT NULL DEFAULT now(), perceived_effort integer CHECK(perceived_effort BETWEEN 1 AND 10),
 notes text NOT NULL DEFAULT '', UNIQUE(owner_id,item_id), FOREIGN KEY(item_id,owner_id) REFERENCES practice_items(id,owner_id)
);
CREATE TABLE songs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), title text NOT NULL,
 reference_asset_id uuid NOT NULL, provenance jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id), FOREIGN KEY(reference_asset_id,owner_id) REFERENCES audio_assets(id,owner_id)
);
CREATE TABLE singing_requests (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), job_id uuid NOT NULL UNIQUE,
 voice_asset_id uuid NOT NULL, melody_asset_id uuid NOT NULL, consent_id uuid NOT NULL,
 params jsonb NOT NULL, adapter_version text NOT NULL, model_revision text NOT NULL,
 FOREIGN KEY(job_id,owner_id) REFERENCES jobs(id,owner_id),
 FOREIGN KEY(voice_asset_id,owner_id) REFERENCES audio_assets(id,owner_id),
 FOREIGN KEY(melody_asset_id,owner_id) REFERENCES audio_assets(id,owner_id),
 FOREIGN KEY(consent_id,owner_id) REFERENCES voice_consents(id,owner_id)
);
CREATE TABLE knowledge_documents (
 id uuid PRIMARY KEY, source_id uuid NOT NULL, language text NOT NULL DEFAULT 'zh' CHECK(language='zh'), source_uri text NOT NULL, title text NOT NULL, license_note text NOT NULL,
 corpus_version text NOT NULL, scope text NOT NULL CHECK(scope IN ('public','private')),
 owner_id uuid REFERENCES users(id), status text NOT NULL CHECK(status IN ('pending','active','retired','failed')),
 content_hash char(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((scope='private' AND owner_id IS NOT NULL) OR (scope='public' AND owner_id IS NULL))
);
CREATE TABLE knowledge_chunks (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES knowledge_documents(id), ordinal integer NOT NULL CHECK(ordinal>=0),
 content text NOT NULL, content_hash char(64) NOT NULL, start_char integer NOT NULL CHECK(start_char>=0), end_char integer NOT NULL,
 UNIQUE(document_id,ordinal), CHECK(end_char>start_char)
);
CREATE TABLE retrieval_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), message_id uuid NOT NULL,
 config jsonb NOT NULL, evidence jsonb NOT NULL, duration_ms integer NOT NULL CHECK(duration_ms>=0),
 degraded_reason text, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(message_id,owner_id) REFERENCES messages(id,owner_id)
);
CREATE TABLE skills (
 id uuid PRIMARY KEY, slug text NOT NULL UNIQUE, scope text NOT NULL CHECK(scope IN ('public','private')),
 owner_id uuid REFERENCES users(id),
 CHECK((scope='private' AND owner_id IS NOT NULL) OR (scope='public' AND owner_id IS NULL))
);
CREATE TABLE skill_versions (
 id uuid PRIMARY KEY, skill_id uuid NOT NULL REFERENCES skills(id), version text NOT NULL,
 status text NOT NULL CHECK(status IN ('draft','candidate','reviewed','published','retired','rejected')),
 manifest jsonb NOT NULL, body text NOT NULL, content_hash char(64) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(skill_id,version)
);
CREATE TABLE skill_reviews (
 id uuid PRIMARY KEY, skill_version_id uuid NOT NULL REFERENCES skill_versions(id), reviewer_subject text NOT NULL,
 decision text NOT NULL CHECK(decision IN ('approve','reject','needs_revision')), report jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memory_events (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), source_type text NOT NULL, source_id uuid NOT NULL,
 event_kind text NOT NULL, source_version bigint NOT NULL CHECK(source_version>=1), sequence_no bigint GENERATED ALWAYS AS IDENTITY,
 payload jsonb NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
 deleted_at timestamptz, UNIQUE(id,owner_id), UNIQUE(owner_id,source_type,source_id,event_kind,source_version)
);
CREATE TABLE memory_facts (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), source_event_id uuid NOT NULL,
 semantic_key text NOT NULL, version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 fact jsonb NOT NULL, status text NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','confirmed','observed','superseded','deleted')),
 expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id), FOREIGN KEY(source_event_id,owner_id) REFERENCES memory_events(id,owner_id)
);
CREATE INDEX memory_fact_owner_status ON memory_facts(owner_id,status,expires_at);
CREATE TABLE memory_profiles (
 owner_id uuid PRIMARY KEY REFERENCES users(id), version integer NOT NULL CHECK(version>=1),
 input_high_watermark bigint NOT NULL DEFAULT 0, input_revision bigint NOT NULL DEFAULT 0,
 summary jsonb NOT NULL, fact_ids jsonb NOT NULL DEFAULT '[]', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE idempotency_keys (
 owner_id uuid NOT NULL REFERENCES users(id), route text NOT NULL, key text NOT NULL, request_hash char(64) NOT NULL,
 status text NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress','completed')), linked_job_id uuid, response_status integer, response jsonb, expires_at timestamptz NOT NULL, PRIMARY KEY(owner_id,route,key)
);
CREATE TABLE outbox_events (
 id uuid PRIMARY KEY, owner_id uuid REFERENCES users(id), event_type text NOT NULL, aggregate_id uuid NOT NULL,
 payload jsonb NOT NULL, schema_version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz
);
CREATE INDEX outbox_unpublished ON outbox_events(created_at) WHERE published_at IS NULL;
CREATE TABLE audit_events (
 id uuid PRIMARY KEY, actor_subject text NOT NULL, owner_id uuid REFERENCES users(id), action text NOT NULL,
 resource_id uuid, trace_id text NOT NULL, detail jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_owner_time ON audit_events(owner_id,created_at DESC);
CREATE TABLE deletion_requests (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), scope jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
 progress jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
ALTER TABLE audio_assets ADD COLUMN origin_job_id uuid;
ALTER TABLE audio_assets ADD COLUMN voice_consent_id uuid;
ALTER TABLE audio_assets ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE audio_assets ADD FOREIGN KEY(origin_job_id,owner_id) REFERENCES jobs(id,owner_id);
ALTER TABLE audio_assets ADD FOREIGN KEY(voice_consent_id,owner_id) REFERENCES voice_consents(id,owner_id);
ALTER TABLE audio_assets ADD CHECK(purpose<>'generated_reference' OR (voice_consent_id IS NOT NULL AND origin_job_id IS NOT NULL));
CREATE TABLE asset_derivations (
 owner_id uuid NOT NULL REFERENCES users(id), parent_asset_id uuid NOT NULL, child_asset_id uuid NOT NULL,
 derivation_kind text NOT NULL CHECK(derivation_kind IN ('normalization','source_separation','singing','reference_copy')),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(parent_asset_id,child_asset_id),
 CHECK(parent_asset_id<>child_asset_id),
 FOREIGN KEY(parent_asset_id,owner_id) REFERENCES audio_assets(id,owner_id),
 FOREIGN KEY(child_asset_id,owner_id) REFERENCES audio_assets(id,owner_id)
);

CREATE TABLE workflow_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), session_id uuid NOT NULL,
 root_run_id uuid NOT NULL, parent_run_id uuid, status text NOT NULL CHECK(status IN ('CREATED','RUNNING','WAIT_JOB','WAIT_USER_INPUT','COMPLETED','FAILED','CANCELLED')),
 action_kind text NOT NULL, action_snapshot jsonb NOT NULL, version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 checkpoint_ref text, current_node text, tool_attempts integer NOT NULL DEFAULT 0 CHECK(tool_attempts>=0),
 business_steps integer NOT NULL DEFAULT 0 CHECK(business_steps>=0), resumes integer NOT NULL DEFAULT 0 CHECK(resumes>=0),
 expires_at timestamptz NOT NULL, trace_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id), FOREIGN KEY(session_id,owner_id) REFERENCES sessions(id,owner_id),
 FOREIGN KEY(parent_run_id,owner_id) REFERENCES workflow_runs(id,owner_id),
 FOREIGN KEY(root_run_id,owner_id) REFERENCES workflow_runs(id,owner_id)
);
CREATE TABLE workflow_steps (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), run_id uuid NOT NULL, step_key text NOT NULL,
 agent text NOT NULL CHECK(agent IN ('knowledge','assessment','planning','singing')),
 status text NOT NULL CHECK(status IN ('pending','running','waiting','succeeded','failed','cancelled')),
 attempt integer NOT NULL DEFAULT 0 CHECK(attempt>=0), effect_key text NOT NULL, input_snapshot jsonb NOT NULL,
 output_refs jsonb NOT NULL DEFAULT '[]', error_code text, version integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(run_id,step_key), UNIQUE(owner_id,effect_key), FOREIGN KEY(run_id,owner_id) REFERENCES workflow_runs(id,owner_id)
);
CREATE TABLE workflow_waits (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), run_id uuid NOT NULL,
 wait_kind text NOT NULL CHECK(wait_kind IN ('job','user_input')), job_id uuid, pending_schema jsonb NOT NULL,
 resume_token_hash char(64), token_key_version text, version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 consumed_at timestamptz, consumed_request_key text, expires_at timestamptz NOT NULL,
 CHECK((wait_kind='job' AND job_id IS NOT NULL AND resume_token_hash IS NULL) OR (wait_kind='user_input' AND job_id IS NULL AND resume_token_hash IS NOT NULL AND token_key_version IS NOT NULL)),
 FOREIGN KEY(run_id,owner_id) REFERENCES workflow_runs(id,owner_id), FOREIGN KEY(job_id,owner_id) REFERENCES jobs(id,owner_id)
);
CREATE UNIQUE INDEX workflow_one_wait ON workflow_waits(run_id) WHERE consumed_at IS NULL;
CREATE TABLE job_attempts (
 job_id uuid NOT NULL REFERENCES jobs(id), attempt integer NOT NULL CHECK(attempt>=1), claim_token uuid NOT NULL UNIQUE,
 worker_id text NOT NULL, started_at timestamptz NOT NULL, lease_until timestamptz NOT NULL,
 finished_at timestamptz, status text NOT NULL CHECK(status IN ('running','succeeded','failed','expired','cancelled')),
 temporary_object_prefix text, provider_request_id text, error_code text, PRIMARY KEY(job_id,attempt)
);
ALTER TABLE jobs ADD COLUMN workflow_id uuid;
ALTER TABLE jobs ADD FOREIGN KEY(workflow_id,owner_id) REFERENCES workflow_runs(id,owner_id);
CREATE TABLE consent_grants (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 purpose text NOT NULL CHECK(purpose IN ('analysis','practice_storage','cross_session_memory','research_improvement','singing_reference')),
 status text NOT NULL CHECK(status IN ('granted','refused','revoked')), text_version text NOT NULL,
 version integer NOT NULL DEFAULT 1 CHECK(version>=1), created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
 UNIQUE(id,owner_id), CHECK((status='revoked' AND revoked_at IS NOT NULL) OR (status<>'revoked' AND revoked_at IS NULL))
);
CREATE UNIQUE INDEX consent_one_active_purpose ON consent_grants(owner_id,purpose) WHERE status='granted';
CREATE TABLE retention_preferences (
 owner_id uuid PRIMARY KEY REFERENCES users(id), raw_audio_days integer NOT NULL DEFAULT 30 CHECK(raw_audio_days IN (7,30,90,180)),
 report_days integer NOT NULL DEFAULT 180 CHECK(report_days IN (7,30,90,180)), technical_log_days integer NOT NULL DEFAULT 30 CHECK(technical_log_days=30),
 backup_max_days integer NOT NULL DEFAULT 30 CHECK(backup_max_days=30), version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_sources (
 id uuid PRIMARY KEY, source_uri text NOT NULL, title text NOT NULL, author text NOT NULL, language text NOT NULL CHECK(language='zh'),
 license_id text NOT NULL, license_evidence jsonb NOT NULL, allowed_purposes jsonb NOT NULL,
 snapshot_hash char(64) NOT NULL, retrieved_at timestamptz NOT NULL, withdrawn_at timestamptz,
 CHECK(jsonb_typeof(license_evidence)='object'), CHECK(jsonb_typeof(allowed_purposes)='array')
);
CREATE TABLE corpus_versions (
 version text PRIMARY KEY, owner_id uuid REFERENCES users(id), channel text NOT NULL DEFAULT 'zh_research',
 scope text NOT NULL CHECK(scope IN ('public','private')), language text NOT NULL CHECK(language='zh'),
 state text NOT NULL CHECK(state IN ('building','checked','active','retired','failed')),
 revision integer NOT NULL DEFAULT 1 CHECK(revision>=1), review_level text NOT NULL CHECK(review_level IN ('candidate','evidence_checked')),
 expert_verified boolean NOT NULL DEFAULT false CHECK(expert_verified=false), manifest jsonb NOT NULL,
 vector_projection_ready boolean NOT NULL DEFAULT false, graph_projection_ready boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), activated_at timestamptz,
 CHECK((scope='private' AND owner_id IS NOT NULL) OR (scope='public' AND owner_id IS NULL)),
 CHECK(state<>'active' OR (review_level='evidence_checked' AND vector_projection_ready AND graph_projection_ready))
);
CREATE UNIQUE INDEX active_public_corpus ON corpus_versions(channel) WHERE state='active' AND owner_id IS NULL;
CREATE UNIQUE INDEX active_private_corpus ON corpus_versions(owner_id,channel) WHERE state='active' AND owner_id IS NOT NULL;
ALTER TABLE knowledge_documents ADD FOREIGN KEY(source_id) REFERENCES knowledge_sources(id);
ALTER TABLE knowledge_documents ADD FOREIGN KEY(corpus_version) REFERENCES corpus_versions(version);
CREATE TABLE corpus_reviews (
 id uuid PRIMARY KEY, corpus_version text NOT NULL REFERENCES corpus_versions(version), resource_id uuid NOT NULL,
 resource_kind text NOT NULL CHECK(resource_kind IN ('source','document','chunk','entity','relation','exercise')),
 decision text NOT NULL CHECK(decision IN ('evidence_checked','reject','needs_revision')),
 verifier_revision text NOT NULL, evidence jsonb NOT NULL, limitations jsonb NOT NULL DEFAULT '[]',
 reviewer_subject text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE seed_id_mappings (
 seed_version text NOT NULL, resource_kind text NOT NULL, seed_id text NOT NULL, production_id uuid NOT NULL,
 source_hash char(64) NOT NULL, namespace_uuid uuid NOT NULL, PRIMARY KEY(seed_version,resource_kind,seed_id), UNIQUE(resource_kind,production_id)
);
CREATE TABLE graph_entities (
 id uuid PRIMARY KEY, corpus_version text NOT NULL REFERENCES corpus_versions(version),
 entity_type text NOT NULL CHECK(entity_type IN ('VocalConcept','Technique','AudioIssue','Exercise','SongSegment','PracticeRecord')),
 canonical_label text NOT NULL, aliases jsonb NOT NULL DEFAULT '[]', payload jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('candidate','evidence_checked','retired')), UNIQUE(id,corpus_version)
);
CREATE TABLE chunk_entity_links (
 corpus_version text NOT NULL REFERENCES corpus_versions(version), chunk_id uuid NOT NULL REFERENCES knowledge_chunks(id), entity_id uuid NOT NULL,
 start_char integer NOT NULL CHECK(start_char>=0), end_char integer NOT NULL, evidence_text text NOT NULL,
 link_confidence double precision CHECK(link_confidence>=0 AND link_confidence<=1), PRIMARY KEY(chunk_id,entity_id,start_char),
 CHECK(end_char>start_char), FOREIGN KEY(entity_id,corpus_version) REFERENCES graph_entities(id,corpus_version)
);
CREATE TABLE graph_relations (
 id uuid PRIMARY KEY, corpus_version text NOT NULL REFERENCES corpus_versions(version), subject_id uuid NOT NULL, object_id uuid NOT NULL,
 predicate text NOT NULL CHECK(predicate IN ('RELATED_TO','REQUIRES','ADDRESSES','ILLUSTRATES','SUPPORTED_BY','OBSERVED_IN')),
 source_chunk_id uuid NOT NULL REFERENCES knowledge_chunks(id), start_char integer NOT NULL CHECK(start_char>=0), end_char integer NOT NULL,
 conditions jsonb NOT NULL DEFAULT '{}', review_level text NOT NULL CHECK(review_level='evidence_checked'),
 retired_at timestamptz, CHECK(end_char>start_char),
 FOREIGN KEY(subject_id,corpus_version) REFERENCES graph_entities(id,corpus_version),
 FOREIGN KEY(object_id,corpus_version) REFERENCES graph_entities(id,corpus_version)
);
CREATE TABLE memory_fact_sources (
 owner_id uuid NOT NULL REFERENCES users(id), fact_id uuid NOT NULL, event_id uuid NOT NULL, PRIMARY KEY(fact_id,event_id),
 FOREIGN KEY(fact_id,owner_id) REFERENCES memory_facts(id,owner_id), FOREIGN KEY(event_id,owner_id) REFERENCES memory_events(id,owner_id)
);
CREATE TABLE memory_profile_sources (
 owner_id uuid NOT NULL REFERENCES memory_profiles(owner_id), fact_id uuid NOT NULL, profile_version integer NOT NULL CHECK(profile_version>=1),
 PRIMARY KEY(owner_id,profile_version,fact_id), FOREIGN KEY(fact_id,owner_id) REFERENCES memory_facts(id,owner_id)
);
CREATE TABLE memory_compaction_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), input_high_watermark bigint NOT NULL CHECK(input_high_watermark>=0),
 expected_revision bigint NOT NULL CHECK(expected_revision>=1), consent_revision bigint NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','running','committed','stale','failed')),
 extractor_revision text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(owner_id,input_high_watermark,expected_revision)
);
CREATE TABLE deletion_ledger (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), resource_kind text NOT NULL, resource_id uuid,
 semantic_key_hash char(64) NOT NULL, data_revision bigint NOT NULL CHECK(data_revision>=1),
 created_at timestamptz NOT NULL DEFAULT now(), purge_after timestamptz NOT NULL, UNIQUE(owner_id,resource_kind,semantic_key_hash)
);
CREATE TABLE event_consumptions (
 consumer text NOT NULL, event_id uuid NOT NULL REFERENCES outbox_events(id), consumed_at timestamptz NOT NULL DEFAULT now(),
 result_ref uuid, PRIMARY KEY(consumer,event_id)
);
CREATE INDEX workflow_owner_status ON workflow_runs(owner_id,status,created_at);
CREATE INDEX wait_job_pending ON workflow_waits(job_id) WHERE consumed_at IS NULL;
CREATE INDEX memory_events_owner_sequence ON memory_events(owner_id,sequence_no);
CREATE INDEX deletion_owner_revision ON deletion_ledger(owner_id,data_revision);


CREATE TABLE provider_budgets (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), provider text NOT NULL,
 currency char(3) NOT NULL, period_day date NOT NULL, limit_minor bigint NOT NULL CHECK(limit_minor>=0),
 reserved_minor bigint NOT NULL DEFAULT 0 CHECK(reserved_minor>=0), spent_minor bigint NOT NULL DEFAULT 0 CHECK(spent_minor>=0),
 version integer NOT NULL DEFAULT 1 CHECK(version>=1), UNIQUE(owner_id,provider,currency,period_day), UNIQUE(id,owner_id),
 CHECK(reserved_minor+spent_minor<=limit_minor)
);
CREATE TABLE provider_reservations (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), budget_id uuid NOT NULL, effect_key text NOT NULL,
 max_cost_minor bigint NOT NULL CHECK(max_cost_minor>=0), actual_cost_minor bigint CHECK(actual_cost_minor>=0 AND actual_cost_minor<=max_cost_minor),
 state text NOT NULL CHECK(state IN ('reserved','submitted','unknown','settled','released')),
 provider_request_id text, version integer NOT NULL DEFAULT 1 CHECK(version>=1), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,effect_key), FOREIGN KEY(budget_id,owner_id) REFERENCES provider_budgets(id,owner_id),
 CHECK((state='settled' AND actual_cost_minor IS NOT NULL) OR (state<>'settled' AND actual_cost_minor IS NULL))
);
ALTER TABLE practice_plans ADD UNIQUE(owner_id,family_id,revision);
CREATE UNIQUE INDEX plan_family_one_active ON practice_plans(owner_id,family_id) WHERE status='active';

COMMIT;
-- Service transactions must enforce owner/purpose/revision, same-corpus links, state transitions, lease fencing and aggregate minute limits.
-- RLS is not enabled; every repository query MUST constrain trusted owner. Checkpointer internal tables are managed by its adapter, separately versioned.
