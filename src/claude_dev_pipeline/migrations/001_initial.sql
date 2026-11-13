CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  repo TEXT,
  issue_ref TEXT,
  pipeline_version TEXT,
  started_at TEXT,
  ended_at TEXT,
  status TEXT,
  risk_tier TEXT,
  review_depth TEXT,
  run_cap_usd REAL,
  outcome TEXT,
  verified_by TEXT,
  intake_kind TEXT,
  intake_ref TEXT,
  pr_url TEXT
);

CREATE TABLE stages (
  stage_id INTEGER PRIMARY KEY,
  run_id TEXT REFERENCES runs(run_id),
  name TEXT,
  ticket TEXT,
  round INTEGER,
  attempt INTEGER,
  provider TEXT,
  model TEXT,
  effort TEXT,
  session_id TEXT,
  agent_id TEXT,
  agent_version INTEGER,
  started_at TEXT,
  ended_at TEXT,
  active_seconds REAL,
  wall_seconds REAL,
  status TEXT,
  stop_reason TEXT,
  gate_message TEXT,
  artifact_sha256 TEXT,
  budget_cents INTEGER,
  model_called INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE usage (
  stage_id INTEGER REFERENCES stages(stage_id),
  thread_id TEXT,
  input_tokens INTEGER,
  cache_write_tokens INTEGER,
  cache_read_tokens INTEGER,
  output_tokens INTEGER,
  web_searches INTEGER,
  list_cost_usd REAL,
  runtime_cost_usd REAL
);

CREATE TABLE findings (
  finding_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs(run_id),
  round INTEGER,
  finder TEXT,
  file TEXT,
  line_start INTEGER,
  line_end INTEGER,
  head_sha TEXT,
  bug_class TEXT,
  severity TEXT,
  tag TEXT,
  confidence INTEGER,
  is_security INTEGER,
  fingerprint TEXT,
  survived INTEGER,
  summary TEXT
);

CREATE INDEX findings_fp ON findings(fingerprint);

CREATE TABLE votes (
  finding_id TEXT REFERENCES findings(finding_id),
  voter TEXT,
  model_family TEXT,
  real INTEGER,
  confidence INTEGER,
  severity TEXT,
  evidence TEXT
);

CREATE TABLE resolutions (
  finding_id TEXT REFERENCES findings(finding_id),
  resolution TEXT,
  fix_sha TEXT,
  reason TEXT,
  decided_by TEXT,
  decided_at TEXT
);

CREATE TABLE retro_items (
  item_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs(run_id),
  category TEXT,
  severity TEXT,
  evidence TEXT,
  proposed_change TEXT,
  target TEXT,
  expected_metric TEXT,
  status TEXT,
  decided_at TEXT,
  follow_up_run_id TEXT
);

CREATE TABLE incidents (
  incident_id TEXT PRIMARY KEY,
  run_id TEXT,
  pr_number INTEGER,
  kind TEXT,
  note TEXT,
  created_at TEXT
);

CREATE TABLE run_metrics (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
  success INTEGER,
  first_try_success INTEGER,
  loops INTEGER,
  cost_usd REAL,
  wall_seconds REAL,
  trajectory_violations INTEGER,
  hidden_failure INTEGER,
  owner_interventions INTEGER,
  rounds_to_pass INTEGER,
  recorded_at TEXT,
  tier TEXT,
  escalated INTEGER
);
