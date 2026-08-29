-- ============================================================
-- Agentic RAG Platform — Shared Database Schema
-- Source of truth: Final Implementation Plan, Sections 4 & 8
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- SHARED TABLES (Section 4.1)
-- ============================================================

CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vertical        TEXT NOT NULL,
    filename        TEXT NOT NULL,
    source          TEXT,
    content_hash    TEXT NOT NULL,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE kb_sync_state (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vertical        TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    UNIQUE (vertical, file_path)
);

CREATE TABLE agent_runs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vertical            TEXT NOT NULL,
    trigger_type        TEXT NOT NULL,
    input_document_id   UUID REFERENCES documents(id),
    status              TEXT NOT NULL,
    confidence          FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_decisions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID NOT NULL REFERENCES agent_runs(id),
    step_type   TEXT NOT NULL CHECK (step_type IN ('retrieval', 'tool_call', 'llm_reasoning', 'action', 'escalation')),
    detail      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_runs_vertical_status ON agent_runs (vertical, status);

CREATE TABLE escalations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID NOT NULL REFERENCES agent_runs(id),
    reason      TEXT,
    assigned_to TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID NOT NULL REFERENCES agent_runs(id),
    recipient   TEXT NOT NULL,
    message     TEXT NOT NULL,
    read        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- VECTOR DB TABLE (Section 4.2)
-- source_id is a generic FK to whichever relational record this
-- chunk came from (documents, employees, action_items, etc.) —
-- deliberately NOT a hard FK constraint, since it can point to
-- different tables depending on vertical/source_type.
-- all-MiniLM-L6-v2 produces 384-dimension embeddings.
-- ============================================================

CREATE TABLE embeddings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vertical        TEXT NOT NULL,
    source_type     TEXT NOT NULL CHECK (source_type IN (
                        'postmortem', 'employee_profile', 'contract_clause',
                        'action_item', 'owner_activity'
                    )),
    source_id       UUID,
    chunk_text      TEXT NOT NULL,
    embedding       VECTOR(384) NOT NULL,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_embeddings_vector ON embeddings USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_embeddings_vertical_source ON embeddings (vertical, source_type);

-- ============================================================
-- VERTICAL 1 — Post-Incident Knowledge Synthesis (Section 8.1)
-- ============================================================

CREATE TABLE incidents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    root_cause_tag  TEXT,
    service         TEXT,
    date            DATE,
    doc_id          UUID REFERENCES documents(id)
);

CREATE TABLE incident_tickets (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id                  UUID NOT NULL REFERENCES agent_runs(id),
    title                   TEXT NOT NULL,
    linked_incident_ids     UUID[],
    status                  TEXT NOT NULL DEFAULT 'open'
);

-- ============================================================
-- VERTICAL 2 — Internal Mobility & Skill-Gap Matching (Section 8.2)
-- ============================================================

CREATE TABLE employees (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL,
    department          TEXT,
    years_experience    INT,
    location            TEXT,
    profile_text        TEXT
);

CREATE TABLE roles (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    description     TEXT,
    department      TEXT,
    min_experience  INT,
    location        TEXT,
    posted_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE role_matches (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id          UUID NOT NULL REFERENCES agent_runs(id),
    role_id         UUID NOT NULL REFERENCES roles(id),
    employee_id     UUID NOT NULL REFERENCES employees(id),
    rank            INT,
    rationale       TEXT,
    confidence      FLOAT,
    notified        BOOLEAN NOT NULL DEFAULT FALSE
);

-- Backing data for the check_capacity() tool — a dynamic workload
-- signal, not a static field on employees (per design correction:
-- availability must never be a hard filter on retrieval).
CREATE TABLE employee_workload (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id     UUID NOT NULL REFERENCES employees(id),
    utilization_pct INT,
    free_by_date    DATE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- VERTICAL 3 — Contract Obligation & Renewal Tracking (Section 8.3)
-- ============================================================

CREATE TABLE contracts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID REFERENCES documents(id),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    vendor_name     TEXT
);

CREATE TABLE obligations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id         UUID NOT NULL REFERENCES contracts(id),
    description         TEXT NOT NULL,
    obligation_date     DATE,
    type                TEXT,
    confidence          FLOAT,
    reminder_created    BOOLEAN NOT NULL DEFAULT FALSE
);

-- ============================================================
-- VERTICAL 4 — Meeting Action-Item Enforcement (Section 8.4)
-- ============================================================

CREATE TABLE meetings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID REFERENCES documents(id),
    meeting_date    DATE
);

CREATE TABLE action_items (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    meeting_id          UUID NOT NULL REFERENCES meetings(id),
    description         TEXT NOT NULL,
    owner               TEXT NOT NULL,
    deadline            DATE,
    status              TEXT NOT NULL DEFAULT 'open',
    nudge_count         INT NOT NULL DEFAULT 0,
    escalated           BOOLEAN NOT NULL DEFAULT FALSE,
    is_recurring        BOOLEAN NOT NULL DEFAULT FALSE,
    recurring_from      UUID REFERENCES action_items(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_action_items_status_deadline ON action_items (status, deadline);