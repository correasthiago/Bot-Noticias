-- Migration 0001: schema inicial da Central Universal V0.1
-- Todas as tabelas sao STRICT. foreign_keys=ON e definido pela conexao
-- (ver central_universal/persistence/db.py), nao pelo schema.

CREATE TABLE IF NOT EXISTS learner (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS learning_domain (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS competency (
    id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL REFERENCES learning_domain(id),
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE IF NOT EXISTS prerequisite_relation (
    id TEXT PRIMARY KEY,
    competency_id TEXT NOT NULL REFERENCES competency(id),
    prerequisite_id TEXT NOT NULL REFERENCES competency(id),
    created_at TEXT NOT NULL,
    UNIQUE (competency_id, prerequisite_id)
) STRICT;

CREATE TABLE IF NOT EXISTS rule_version (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;

CREATE TABLE IF NOT EXISTS learning_session (
    id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL REFERENCES learner(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended'))
) STRICT;

CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES learning_session(id),
    competency_targets TEXT NOT NULL, -- JSON array of competency ids
    activity_type TEXT NOT NULL,
    prompt TEXT NOT NULL,
    support_level TEXT NOT NULL CHECK (support_level IN ('A0','A1','A2','A3')),
    created_at TEXT NOT NULL,
    tutor_provider_event_id TEXT REFERENCES provider_event(id)
) STRICT;

CREATE TABLE IF NOT EXISTS raw_interaction (
    id TEXT PRIMARY KEY,
    activity_id TEXT NOT NULL REFERENCES activity(id),
    session_id TEXT NOT NULL REFERENCES learning_session(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    learner_input TEXT NOT NULL,
    tutor_output TEXT NOT NULL DEFAULT '',
    help_level TEXT NOT NULL CHECK (help_level IN ('A0','A1','A2','A3')),
    production_result TEXT NOT NULL CHECK (production_result IN (
        'spontaneous_correct','spontaneous_self_correction','correct_after_hint',
        'correct_after_external_correction','incorrect','inconclusive'
    )),
    occurred_at TEXT NOT NULL,
    evidence_cluster_id TEXT NOT NULL
) STRICT;

-- Evidencia bruta e imutavel (Principio 4): nenhuma UPDATE/DELETE de
-- aplicacao deve tocar raw_interaction ou evidence_event depois de
-- inseridos. Isso e reforcado por trigger, nao apenas por convencao.
CREATE TRIGGER IF NOT EXISTS trg_raw_interaction_immutable_update
BEFORE UPDATE ON raw_interaction
BEGIN
    SELECT RAISE(ABORT, 'raw_interaction e imutavel: UPDATE proibido');
END;

CREATE TRIGGER IF NOT EXISTS trg_raw_interaction_immutable_delete
BEFORE DELETE ON raw_interaction
BEGIN
    SELECT RAISE(ABORT, 'raw_interaction e imutavel: DELETE proibido');
END;

CREATE TABLE IF NOT EXISTS evidence_event (
    id TEXT PRIMARY KEY,
    raw_interaction_id TEXT NOT NULL REFERENCES raw_interaction(id),
    competency_id TEXT NOT NULL REFERENCES competency(id),
    dimension TEXT NOT NULL CHECK (dimension IN (
        'comprehension','retrieval','accuracy','automaticity','transfer','retention'
    )),
    evidence_type TEXT NOT NULL CHECK (evidence_type IN (
        'positive','negative','contradictory','inconclusive'
    )),
    relation TEXT NOT NULL CHECK (relation IN (
        'target','qualified_incidental','mere_presence'
    )),
    help_level TEXT NOT NULL CHECK (help_level IN ('A0','A1','A2','A3')),
    production_result TEXT NOT NULL CHECK (production_result IN (
        'spontaneous_correct','spontaneous_self_correction','correct_after_hint',
        'correct_after_external_correction','incorrect','inconclusive'
    )),
    evidence_cluster_id TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS trg_evidence_event_immutable_update
BEFORE UPDATE ON evidence_event
BEGIN
    SELECT RAISE(ABORT, 'evidence_event e imutavel: UPDATE proibido');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_event_immutable_delete
BEFORE DELETE ON evidence_event
BEGIN
    SELECT RAISE(ABORT, 'evidence_event e imutavel: DELETE proibido');
END;

CREATE TABLE IF NOT EXISTS evidence_assessment (
    id TEXT PRIMARY KEY,
    evidence_event_id TEXT NOT NULL REFERENCES evidence_event(id),
    rule_version_id TEXT NOT NULL REFERENCES rule_version(id),
    classification TEXT NOT NULL CHECK (classification IN (
        'positive','negative','contradictory','inconclusive'
    )),
    result TEXT NOT NULL,
    confidence REAL NOT NULL,
    justification TEXT NOT NULL,
    alternative_cause TEXT,
    inconclusive INTEGER NOT NULL DEFAULT 0 CHECK (inconclusive IN (0, 1)),
    evaluator_provider_event_id TEXT REFERENCES provider_event(id),
    created_at TEXT NOT NULL
) STRICT;

-- Avaliacoes tambem sao versionadas e imutaveis (Principio 5): uma nova
-- avaliacao e sempre uma nova linha, nunca uma correcao da anterior.
CREATE TRIGGER IF NOT EXISTS trg_evidence_assessment_immutable_update
BEFORE UPDATE ON evidence_assessment
BEGIN
    SELECT RAISE(ABORT, 'evidence_assessment e imutavel: UPDATE proibido');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_assessment_immutable_delete
BEFORE DELETE ON evidence_assessment
BEGIN
    SELECT RAISE(ABORT, 'evidence_assessment e imutavel: DELETE proibido');
END;

-- competency_state e projecao DERIVADA e recalculavel (Principio 6,
-- Secao 14): append-only, o "estado atual" e a linha de computed_at mais
-- recente por (competency_id, dimension). Pode ser inteiramente apagada e
-- reconstruida a partir de raw_interaction + evidence_event +
-- evidence_assessment + rule_version sem perda de informacao.
CREATE TABLE IF NOT EXISTS competency_state (
    id TEXT PRIMARY KEY,
    competency_id TEXT NOT NULL REFERENCES competency(id),
    dimension TEXT NOT NULL CHECK (dimension IN (
        'comprehension','retrieval','accuracy','automaticity','transfer','retention'
    )),
    state TEXT NOT NULL CHECK (state IN (
        'not_assessed','insufficient_evidence','acquiring','demonstrated','consolidated'
    )),
    possible_regression INTEGER NOT NULL DEFAULT 0 CHECK (possible_regression IN (0, 1)),
    has_unresolved_contradiction INTEGER NOT NULL DEFAULT 0 CHECK (has_unresolved_contradiction IN (0, 1)),
    last_evidence_assessment_id TEXT REFERENCES evidence_assessment(id),
    rule_version_id TEXT NOT NULL REFERENCES rule_version(id),
    computed_at TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE INDEX IF NOT EXISTS idx_competency_state_lookup
    ON competency_state (competency_id, dimension, computed_at);

CREATE TABLE IF NOT EXISTS memory_state (
    id TEXT PRIMARY KEY,
    competency_id TEXT NOT NULL UNIQUE REFERENCES competency(id),
    fsrs_card_json TEXT NOT NULL,
    due_at TEXT,
    stability REAL,
    difficulty REAL,
    card_state TEXT NOT NULL CHECK (card_state IN ('learning','review','relearning')),
    last_review_at TEXT,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS memory_review_log (
    id TEXT PRIMARY KEY,
    competency_id TEXT NOT NULL REFERENCES competency(id),
    memory_state_id TEXT NOT NULL REFERENCES memory_state(id),
    review_datetime TEXT NOT NULL,
    rating INTEGER NOT NULL,
    fsrs_review_log_json TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS decision_event (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES learning_session(id),
    learner_id TEXT NOT NULL REFERENCES learner(id),
    competency_id TEXT REFERENCES competency(id),
    routing TEXT NOT NULL CHECK (routing IN ('SKIP','VALIDATE','STUDY')),
    decision_type TEXT,
    rule_applied TEXT NOT NULL,
    justification TEXT NOT NULL CHECK (length(trim(justification)) > 0),
    rule_version_id TEXT NOT NULL REFERENCES rule_version(id),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS provider_event (
    id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL,
    model TEXT NOT NULL,
    function TEXT NOT NULL CHECK (function IN ('tutor','evaluator')),
    config_version TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    latency_ms REAL,
    tokens_used INTEGER,
    cost REAL,
    error_message TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS backup_event (
    id TEXT PRIMARY KEY,
    backup_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    size_bytes INTEGER,
    retention_note TEXT NOT NULL DEFAULT '',
    error_message TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_evidence_event_competency ON evidence_event (competency_id, dimension);
CREATE INDEX IF NOT EXISTS idx_evidence_event_raw ON evidence_event (raw_interaction_id);
CREATE INDEX IF NOT EXISTS idx_evidence_assessment_event ON evidence_assessment (evidence_event_id);
CREATE INDEX IF NOT EXISTS idx_raw_interaction_activity ON raw_interaction (activity_id);
CREATE INDEX IF NOT EXISTS idx_activity_session ON activity (session_id);
CREATE INDEX IF NOT EXISTS idx_decision_event_competency ON decision_event (competency_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_review_log_competency ON memory_review_log (competency_id, review_datetime);
