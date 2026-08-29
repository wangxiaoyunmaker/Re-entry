PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runtime_sessions (
  session_id TEXT PRIMARY KEY,
  participant_id TEXT NOT NULL,
  cwd TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  plugin_version TEXT NOT NULL,
  platform_capabilities_json TEXT
);

CREATE TABLE IF NOT EXISTS raw_events (
  event_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_id TEXT,
  tool_use_id TEXT,
  hook_event_name TEXT,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  collector_seq INTEGER NOT NULL,
  payload_hash TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  content_text TEXT,
  content_json TEXT,
  raw_payload_json TEXT,
  UNIQUE(session_id, hook_event_name, turn_id, tool_use_id, payload_hash)
);

CREATE INDEX IF NOT EXISTS raw_events_session_seq_idx ON raw_events(session_id, collector_seq);
CREATE INDEX IF NOT EXISTS raw_events_turn_idx ON raw_events(session_id, turn_id, collector_seq);

CREATE TABLE IF NOT EXISTS issue_chains (
  issue_chain_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  issue_key TEXT NOT NULL,
  issue_summary TEXT NOT NULL,
  status TEXT NOT NULL,
  unmet_report_count INTEGER NOT NULL DEFAULT 0,
  cooldown_until_user_turn INTEGER NOT NULL DEFAULT 0,
  first_event_id TEXT NOT NULL,
  last_event_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_classifications (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  issue_chain_id TEXT,
  model TEXT,
  schema_version TEXT NOT NULL,
  output_json TEXT NOT NULL,
  confidence TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stall_assessments (
  id TEXT PRIMARY KEY,
  issue_chain_id TEXT NOT NULL,
  as_of_event_id TEXT NOT NULL,
  eligible INTEGER NOT NULL,
  reason_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reentry_runs (
  reentry_run_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  issue_chain_id TEXT,
  trigger_type TEXT NOT NULL,
  trigger_event_id TEXT,
  state TEXT NOT NULL,
  state_version INTEGER NOT NULL DEFAULT 1,
  invited_at TEXT,
  started_at TEXT,
  completed_at TEXT,
  dismissed_at TEXT,
  completion_reason TEXT,
  completed_by_action TEXT,
  pre_prompt_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reentry_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  snapshot_version INTEGER NOT NULL,
  summary_json TEXT NOT NULL,
  as_of_event_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(reentry_run_id, snapshot_version)
);

CREATE TABLE IF NOT EXISTS reentry_reconstructions (
  reconstruction_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  snapshot_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  output_json TEXT,
  failure_code TEXT,
  failure_message TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(reentry_run_id, snapshot_version)
);

CREATE TABLE IF NOT EXISTS agent_claims (
  claim_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  reconstruction_id TEXT NOT NULL,
  claim_kind TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  supporting_evidence_ids_json TEXT NOT NULL,
  verification_status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS agent_claims_run_idx ON agent_claims(reentry_run_id, created_at);

CREATE TABLE IF NOT EXISTS context_reviews (
  review_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  reconstruction_id TEXT NOT NULL,
  item_type TEXT NOT NULL,
  item_id TEXT NOT NULL,
  action TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  interaction_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS context_reviews_run_idx ON context_reviews(reentry_run_id, created_at);

CREATE TABLE IF NOT EXISTS user_edits (
  edit_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  edit_type TEXT NOT NULL,
  target_id TEXT,
  before_text TEXT,
  after_text TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_drafts (
  prompt_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  structured_json TEXT,
  generated_text TEXT NOT NULL,
  user_edited_text TEXT,
  created_at TEXT NOT NULL,
  copied_at TEXT,
  edited_at TEXT,
  sent_at TEXT,
  review_version INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'GENERATED'
);

CREATE TABLE IF NOT EXISTS investigations (
  investigation_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  target_review_item_id TEXT,
  target_item_type TEXT NOT NULL,
  question_to_verify TEXT NOT NULL,
  evidence_requirement TEXT NOT NULL,
  relevant_context_json TEXT NOT NULL,
  constraints_json TEXT NOT NULL,
  expected_observable_result TEXT NOT NULL,
  generated_prompt TEXT NOT NULL,
  edited_prompt TEXT,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  source_review_ids_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investigation_results (
  result_id TEXT PRIMARY KEY,
  investigation_id TEXT NOT NULL,
  result_event_ids_json TEXT NOT NULL,
  evidence_candidate_ids_json TEXT NOT NULL,
  evidence_candidates_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS investigations_run_idx ON investigations(reentry_run_id, created_at);

CREATE TABLE IF NOT EXISTS survey_responses (
  survey_id TEXT PRIMARY KEY,
  reentry_run_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  question_set_version TEXT NOT NULL,
  responses_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(reentry_run_id, phase)
);

CREATE TABLE IF NOT EXISTS ui_actions (
  interaction_id TEXT PRIMARY KEY,
  reentry_run_id TEXT,
  session_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  payload_json TEXT,
  state_version INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_runs (
  llm_run_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  reentry_run_id TEXT,
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  input_refs_json TEXT NOT NULL,
  output_json TEXT,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  error_text TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_errors (
  error_id TEXT PRIMARY KEY,
  session_id TEXT,
  reentry_run_id TEXT,
  component TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  recoverable INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
