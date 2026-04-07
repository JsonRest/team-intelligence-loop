-- =============================================================
-- Team Intelligence Loop — AlloyDB Schema
-- Run after Auth Proxy is running on 127.0.0.1:5432:
--   psql -h 127.0.0.1 -p 5432 -U postgres -d til_db -f schema.sql
-- =============================================================

-- Extensions (AlloyDB has pgvector pre-installed)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Team members ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS team_members (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         VARCHAR(100) NOT NULL,
    email        VARCHAR(255) UNIQUE NOT NULL,
    calendar_id  VARCHAR(255),                     -- Google Calendar ID (usually same as email)
    preferences  JSONB DEFAULT '{}',
    created_at   TIMESTAMP DEFAULT NOW()
);

-- ── Raw standup submissions ───────────────────────────────────
CREATE TABLE IF NOT EXISTS standups (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    member_id         UUID REFERENCES team_members(id) ON DELETE CASCADE,
    sprint_day        DATE NOT NULL,
    raw_text          TEXT NOT NULL,
    submitted_at      TIMESTAMP DEFAULT NOW(),
    content_embedding vector(768)                  -- text-embedding-004 via Vertex AI
);

-- ── Structured parsed fields ──────────────────────────────────
CREATE TABLE IF NOT EXISTS parsed_items (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    standup_id    UUID REFERENCES standups(id) ON DELETE CASCADE,
    category      VARCHAR(20) NOT NULL CHECK (category IN ('yesterday', 'today', 'blocker')),
    content       TEXT NOT NULL,
    owner_id      UUID REFERENCES team_members(id),
    blocked_by_id UUID REFERENCES team_members(id)   -- populated for blockers only
);

-- ── Cross-person blockers ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS blockers (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id              UUID REFERENCES team_members(id),       -- person who is blocked
    blocked_person_id     UUID REFERENCES team_members(id),       -- person causing the block
    description           TEXT NOT NULL,
    status                VARCHAR(20) DEFAULT 'active'
                              CHECK (status IN ('active', 'scheduled', 'resolved')),
    resolution            TEXT,
    sprint_day            DATE NOT NULL,
    created_at            TIMESTAMP DEFAULT NOW(),
    resolved_at           TIMESTAMP,
    description_embedding vector(768)
);

-- ── Decision journal ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS decisions (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision          TEXT NOT NULL,
    rationale         TEXT,
    alternatives      TEXT,
    expected_outcome  TEXT,
    actual_outcome    TEXT,                           -- filled at review_date
    review_date       DATE,
    sprint_day        DATE,
    created_at        TIMESTAMP DEFAULT NOW(),
    content_embedding vector(768)
);

-- ── Indexes ───────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_standups_sprint_day   ON standups(sprint_day);
CREATE INDEX IF NOT EXISTS idx_standups_member       ON standups(member_id);
CREATE INDEX IF NOT EXISTS idx_parsed_standup        ON parsed_items(standup_id);
CREATE INDEX IF NOT EXISTS idx_parsed_category       ON parsed_items(category);
CREATE INDEX IF NOT EXISTS idx_blockers_sprint_day   ON blockers(sprint_day);
CREATE INDEX IF NOT EXISTS idx_blockers_status       ON blockers(status);
CREATE INDEX IF NOT EXISTS idx_decisions_review      ON decisions(review_date);
CREATE INDEX IF NOT EXISTS idx_decisions_sprint      ON decisions(sprint_day);

-- Vector indexes for semantic search (pgvector ivfflat)
CREATE INDEX IF NOT EXISTS idx_blockers_embedding ON blockers
    USING ivfflat (description_embedding vector_cosine_ops) WITH (lists = 10);
CREATE INDEX IF NOT EXISTS idx_decisions_embedding ON decisions
    USING ivfflat (content_embedding vector_cosine_ops) WITH (lists = 10);

-- ── Seed: sample team (replace with real emails before deploying) ──
INSERT INTO team_members (name, email, calendar_id) VALUES
    ('Jesse L.',   'jsliamzon@gmail.com',  'jsliamzon@gmail.com'),
    ('Jesse W.',    'jsliamzon@globe.com.ph',   'jsliamzon@globe.com.ph'),
    ('Amber T.',   'angela.teng@globe.com.ph',  'angela.teng@globe.com.ph'),
    ('Nikko Y.',  'nikko.yabut@globe.com.ph', 'nikko.yabut@globe.com.ph'),
ON CONFLICT (email) DO NOTHING;
