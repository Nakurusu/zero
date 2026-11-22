CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id    TEXT PRIMARY KEY,
    title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS material_snippets (
    id           TEXT PRIMARY KEY,
    topic_id     TEXT NOT NULL REFERENCES topics(id),
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    keywords     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id              TEXT PRIMARY KEY,
    topic_id        TEXT NOT NULL REFERENCES topics(id),
    prompt          TEXT NOT NULL,
    expected        TEXT NOT NULL,
    acceptable      TEXT NOT NULL,
    knowledge_point TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id               TEXT PRIMARY KEY,
    topic_id         TEXT NOT NULL REFERENCES topics(id),
    user_id          TEXT NOT NULL REFERENCES users(id),
    strategy_name    TEXT NOT NULL,
    strategy_version INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id                      TEXT PRIMARY KEY,
    conversation_id         TEXT NOT NULL REFERENCES conversations(id),
    turn_seq                INTEGER NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','interrupted')),
    planned_strategy        TEXT NOT NULL,
    planned_version         INTEGER NOT NULL,
    actual_strategy         TEXT,
    actual_version          INTEGER,
    fallback_reason         TEXT,
    plan_schema_version     INTEGER,
    context_builder_version TEXT,
    plan_doc                TEXT,
    owner_instance          TEXT,
    fence_token             INTEGER NOT NULL DEFAULT 0,
    cancel_requested        INTEGER NOT NULL DEFAULT 0,
    client_msg_id           TEXT,
    parent_turn_id          TEXT,
    error_code              TEXT,
    interrupt_reason        TEXT,
    eval_run_id             TEXT,
    case_id                 TEXT,
    turn_index              INTEGER,
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    UNIQUE (conversation_id, turn_seq),
    UNIQUE (conversation_id, client_msg_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_turns_running_slot
    ON turns (conversation_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    turn_id         TEXT NOT NULL REFERENCES turns(id),
    role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content         TEXT NOT NULL DEFAULT '',
    msg_seq         INTEGER NOT NULL,
    is_partial      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE (conversation_id, msg_seq)
);

CREATE TABLE IF NOT EXISTS model_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id       TEXT NOT NULL REFERENCES turns(id),
    call_seq      INTEGER NOT NULL,
    model_id      TEXT NOT NULL,
    params        TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_ms    INTEGER,
    created_at    TEXT NOT NULL,
    UNIQUE (turn_id, call_seq)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id     TEXT NOT NULL REFERENCES turns(id),
    call_seq    INTEGER NOT NULL,
    tool_name   TEXT NOT NULL,
    args        TEXT NOT NULL,
    result_ref  TEXT,
    latency_ms  INTEGER,
    was_allowed INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (turn_id, call_seq)
);

CREATE TABLE IF NOT EXISTS turn_context (
    turn_id    TEXT NOT NULL REFERENCES turns(id),
    snippet_id TEXT NOT NULL,
    source     TEXT NOT NULL CHECK (source IN ('preselected','tool')),
    PRIMARY KEY (turn_id, snippet_id)
);

CREATE TABLE IF NOT EXISTS learning_state (
    user_id    TEXT NOT NULL REFERENCES users(id),
    topic_id   TEXT NOT NULL REFERENCES topics(id),
    digest     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, topic_id)
);

CREATE TABLE IF NOT EXISTS answer_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    topic_id        TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    turn_id         TEXT NOT NULL,
    question_id     TEXT NOT NULL,
    user_answer     TEXT NOT NULL,
    expected        TEXT NOT NULL,
    correct         INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_liveness (
    instance_uuid  TEXT PRIMARY KEY,
    last_heartbeat TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id             TEXT PRIMARY KEY,
    seed_manifest_hash TEXT NOT NULL,
    model_id           TEXT NOT NULL,
    params             TEXT NOT NULL,
    strategy_versions  TEXT NOT NULL,
    checks_version     TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT
);

CREATE TABLE IF NOT EXISTS eval_check_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    case_id    TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    strategy   TEXT NOT NULL,
    check_name TEXT NOT NULL,
    passed     INTEGER NOT NULL,
    detail     TEXT
);
