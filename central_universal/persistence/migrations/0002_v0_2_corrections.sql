-- Migration 0002: correcoes estruturais V0.2 (Red Team pos-entrega).
--
-- Resumo do que muda e por que, ponto a ponto do pacote de correcao:
--  1) RuleVersion ganha config_json/algorithm_version, vira IMUTAVEL de
--     verdade (trigger), e "qual versao esta ativa" passa a viver em uma
--     tabela-ponteiro mutavel separada (active_rule_version), nunca em uma
--     coluna da propria linha imutavel.
--  2) evidence_cluster: contexto de tentativa computado no SERVIDOR -
--     nunca mais um id arbitrario vindo do chamador.
--  3) raw_interaction ganha evaluation_status (pending/completed) para
--     permitir reprocessamento idempotente, e sua trigger de imutabilidade
--     passa a permitir EXATAMENTE a transicao pending->completed.
--  4) evidence_event perde a coluna evidence_type (classificacao passa a
--     viver exclusivamente em evidence_assessment) e ganha FK real para
--     evidence_cluster.
--  5) evidence_assessment ganha CHECK de consistencia semantica
--     (inconclusive=1 <=> classification='inconclusive').
--  6) projection_generation + active_projection_generation: reconstrucao
--     de CompetencyState deixa de ser destrutiva.
--  7) competency_state ganha generation_id e vira append-only imutavel
--     (trigger), nunca mais apagada em bloco.
--  8) memory_observation: unico gatilho legitimo para revisao FSRS.
--  9) activity ganha is_planned_recall.
-- 10) decision_event, provider_event, memory_review_log, backup_event
--     ganham triggers de imutabilidade.
--
-- Esta migration assume um banco de desenvolvimento pre-lancamento (Secao
-- 1: "o primeiro usuario e uma unica pessoa", ainda nao em producao real).
-- Onde ha dado legado a preservar (bancos de teste/dev ja existentes),
-- fazemos um backfill honesto; nao existe usuario real cujo historico
-- pudesse ser perdido nesta transicao.
--
-- v0.2.1 (pacote de correcao, ponto 4): esta migration inteira roda como
-- UMA transacao atomica (BEGIN.../COMMIT explicitos abaixo) - uma falha
-- em qualquer instrucao desfaz tudo, nunca deixa o banco parcialmente
-- migrado (ver `persistence/migrations.py:_apply_migration_atomically`
-- para por que o BEGIN/COMMIT precisa estar aqui dentro do arquivo, e nao
-- so em Python). `PRAGMA foreign_keys` e ligado/desligado por fora, em
-- Python - a pragma so tem efeito fora de uma transacao aberta.

BEGIN IMMEDIATE;

-- =====================================================================
-- 1) RuleVersion: config_json, algorithm_version, active_rule_version,
--    imutabilidade real.
-- =====================================================================

ALTER TABLE rule_version ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE rule_version ADD COLUMN algorithm_version TEXT NOT NULL DEFAULT 'v1';

CREATE TABLE active_rule_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    rule_version_id TEXT NOT NULL REFERENCES rule_version(id)
) STRICT;

INSERT INTO active_rule_version (id, rule_version_id)
SELECT 1, id FROM rule_version WHERE active = 1 ORDER BY created_at DESC LIMIT 1;

ALTER TABLE rule_version DROP COLUMN active;

CREATE TRIGGER trg_rule_version_immutable_update
BEFORE UPDATE ON rule_version
BEGIN
    SELECT RAISE(ABORT, 'rule_version e imutavel: UPDATE proibido. Crie uma nova versao.');
END;

CREATE TRIGGER trg_rule_version_immutable_delete
BEFORE DELETE ON rule_version
BEGIN
    SELECT RAISE(ABORT, 'rule_version e imutavel: DELETE proibido.');
END;

-- =====================================================================
-- 2) evidence_cluster: contexto de tentativa computado no servidor.
-- =====================================================================

CREATE TABLE evidence_cluster (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES learning_session(id),
    activity_type TEXT NOT NULL,
    context_signature TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'session_interaction',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (session_id, context_signature)
) STRICT;

CREATE INDEX idx_evidence_cluster_session ON evidence_cluster (session_id);

-- Backfill honesto: qualquer evidence_cluster_id livre ja usado por
-- raw_interaction legado vira sua propria EvidenceCluster (mesmo id),
-- preservando todas as referencias existentes sem reescreve-las.
INSERT INTO evidence_cluster (id, session_id, activity_type, context_signature, origin, first_seen_at, last_seen_at)
SELECT
    ri.evidence_cluster_id,
    MIN(ri.session_id),
    MIN(COALESCE((SELECT a.activity_type FROM activity a WHERE a.id = ri.activity_id), 'unknown')),
    ri.evidence_cluster_id,
    'legacy_migration',
    MIN(ri.occurred_at),
    MAX(ri.occurred_at)
FROM raw_interaction ri
GROUP BY ri.evidence_cluster_id;

-- =====================================================================
-- 3) activity: is_planned_recall.
-- =====================================================================

ALTER TABLE activity ADD COLUMN is_planned_recall INTEGER NOT NULL DEFAULT 0 CHECK (is_planned_recall IN (0, 1));

-- =====================================================================
-- 4) raw_interaction: evaluation_status + FK real para evidence_cluster +
--    trigger de imutabilidade que permite so pending->completed.
-- =====================================================================

CREATE TABLE raw_interaction_new (
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
    evidence_cluster_id TEXT NOT NULL REFERENCES evidence_cluster(id),
    evaluation_status TEXT NOT NULL DEFAULT 'pending' CHECK (evaluation_status IN ('pending', 'completed'))
) STRICT;

INSERT INTO raw_interaction_new
    (id, activity_id, session_id, idempotency_key, learner_input, tutor_output,
     help_level, production_result, occurred_at, evidence_cluster_id, evaluation_status)
SELECT
    ri.id, ri.activity_id, ri.session_id, ri.idempotency_key, ri.learner_input, ri.tutor_output,
    ri.help_level, ri.production_result, ri.occurred_at, ri.evidence_cluster_id,
    CASE WHEN EXISTS (SELECT 1 FROM evidence_event ee WHERE ee.raw_interaction_id = ri.id)
         THEN 'completed' ELSE 'pending' END
FROM raw_interaction ri;

DROP TABLE raw_interaction;
ALTER TABLE raw_interaction_new RENAME TO raw_interaction;

CREATE INDEX idx_raw_interaction_activity ON raw_interaction (activity_id);
CREATE INDEX idx_raw_interaction_cluster ON raw_interaction (evidence_cluster_id);
CREATE INDEX idx_raw_interaction_evaluation_status ON raw_interaction (evaluation_status);

-- Permite EXATAMENTE a transicao pending->completed (o "claim" idempotente
-- de avaliacao); qualquer outra alteracao - inclusive tocar em qualquer
-- campo de fato observado - e abortada.
CREATE TRIGGER trg_raw_interaction_immutable_update
BEFORE UPDATE ON raw_interaction
WHEN
    NEW.activity_id IS NOT OLD.activity_id
    OR NEW.session_id IS NOT OLD.session_id
    OR NEW.idempotency_key IS NOT OLD.idempotency_key
    OR NEW.learner_input IS NOT OLD.learner_input
    OR NEW.tutor_output IS NOT OLD.tutor_output
    OR NEW.help_level IS NOT OLD.help_level
    OR NEW.production_result IS NOT OLD.production_result
    OR NEW.occurred_at IS NOT OLD.occurred_at
    OR NEW.evidence_cluster_id IS NOT OLD.evidence_cluster_id
    OR OLD.evaluation_status <> 'pending'
    OR NEW.evaluation_status <> 'completed'
BEGIN
    SELECT RAISE(ABORT, 'raw_interaction e imutavel, exceto a transicao evaluation_status pending->completed');
END;

CREATE TRIGGER trg_raw_interaction_immutable_delete
BEFORE DELETE ON raw_interaction
BEGIN
    SELECT RAISE(ABORT, 'raw_interaction e imutavel: DELETE proibido');
END;

-- =====================================================================
-- 5) evidence_event: remove evidence_type (classificacao so em
--    evidence_assessment); FK real para evidence_cluster.
-- =====================================================================

CREATE TABLE evidence_event_new (
    id TEXT PRIMARY KEY,
    raw_interaction_id TEXT NOT NULL REFERENCES raw_interaction(id),
    competency_id TEXT NOT NULL REFERENCES competency(id),
    dimension TEXT NOT NULL CHECK (dimension IN (
        'comprehension','retrieval','accuracy','automaticity','transfer','retention'
    )),
    relation TEXT NOT NULL CHECK (relation IN (
        'target','qualified_incidental','mere_presence'
    )),
    help_level TEXT NOT NULL CHECK (help_level IN ('A0','A1','A2','A3')),
    production_result TEXT NOT NULL CHECK (production_result IN (
        'spontaneous_correct','spontaneous_self_correction','correct_after_hint',
        'correct_after_external_correction','incorrect','inconclusive'
    )),
    evidence_cluster_id TEXT NOT NULL REFERENCES evidence_cluster(id),
    created_at TEXT NOT NULL
) STRICT;

INSERT INTO evidence_event_new
    (id, raw_interaction_id, competency_id, dimension, relation, help_level,
     production_result, evidence_cluster_id, created_at)
SELECT id, raw_interaction_id, competency_id, dimension, relation, help_level,
       production_result, evidence_cluster_id, created_at
FROM evidence_event;

DROP TABLE evidence_event;
ALTER TABLE evidence_event_new RENAME TO evidence_event;

CREATE INDEX idx_evidence_event_competency ON evidence_event (competency_id, dimension);
CREATE INDEX idx_evidence_event_raw ON evidence_event (raw_interaction_id);
CREATE INDEX idx_evidence_event_cluster ON evidence_event (evidence_cluster_id);

CREATE TRIGGER trg_evidence_event_immutable_update
BEFORE UPDATE ON evidence_event
BEGIN
    SELECT RAISE(ABORT, 'evidence_event e imutavel: UPDATE proibido');
END;

CREATE TRIGGER trg_evidence_event_immutable_delete
BEFORE DELETE ON evidence_event
BEGIN
    SELECT RAISE(ABORT, 'evidence_event e imutavel: DELETE proibido');
END;

-- =====================================================================
-- 6) evidence_assessment: CHECK de consistencia inconclusive<->classification,
--    e limites de confidence/justification.
-- =====================================================================

CREATE TABLE evidence_assessment_new (
    id TEXT PRIMARY KEY,
    evidence_event_id TEXT NOT NULL REFERENCES evidence_event(id),
    rule_version_id TEXT NOT NULL REFERENCES rule_version(id),
    classification TEXT NOT NULL CHECK (classification IN ('positive','negative','contradictory','inconclusive')),
    result TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    justification TEXT NOT NULL CHECK (length(trim(justification)) > 0),
    alternative_cause TEXT,
    inconclusive INTEGER NOT NULL DEFAULT 0 CHECK (inconclusive IN (0, 1)),
    evaluator_provider_event_id TEXT REFERENCES provider_event(id),
    created_at TEXT NOT NULL,
    CHECK (
        (classification = 'inconclusive' AND inconclusive = 1)
        OR (classification <> 'inconclusive' AND inconclusive = 0)
    )
) STRICT;

-- v0.2.1 (correcao do ponto 4): a versao anterior desta migration usava
-- um WHERE para so copiar linhas ja consistentes com o novo CHECK,
-- descartando SILENCIOSAMENTE qualquer avaliacao v0.1 cujo par
-- classification/inconclusive nao batesse (o esquema antigo nao tinha
-- essa restricao, entao dados legados legitimos podiam violar o CHECK
-- novo). Agora TODA avaliacao e preservada, sem excecao: `inconclusive`
-- e recalculado a partir de `classification` (a fonte de verdade) em vez
-- de confiar no valor legado potencialmente divergente. O diagnostico de
-- quantas linhas precisaram ser normalizadas fica registrado em
-- `schema_migrations.notes` (ver `migrations.py:_diagnose_migration`).
INSERT INTO evidence_assessment_new
    (id, evidence_event_id, rule_version_id, classification, result, confidence,
     justification, alternative_cause, inconclusive, evaluator_provider_event_id, created_at)
SELECT
    id, evidence_event_id, rule_version_id, classification, result, confidence,
    justification, alternative_cause,
    CASE WHEN classification = 'inconclusive' THEN 1 ELSE 0 END,
    evaluator_provider_event_id, created_at
FROM evidence_assessment;

DROP TABLE evidence_assessment;
ALTER TABLE evidence_assessment_new RENAME TO evidence_assessment;

CREATE INDEX idx_evidence_assessment_event ON evidence_assessment (evidence_event_id);

CREATE TRIGGER trg_evidence_assessment_immutable_update
BEFORE UPDATE ON evidence_assessment
BEGIN
    SELECT RAISE(ABORT, 'evidence_assessment e imutavel: UPDATE proibido');
END;

CREATE TRIGGER trg_evidence_assessment_immutable_delete
BEFORE DELETE ON evidence_assessment
BEGIN
    SELECT RAISE(ABORT, 'evidence_assessment e imutavel: DELETE proibido');
END;

-- =====================================================================
-- 7) projection_generation / active_projection_generation / competency_state
--    com generation_id, append-only imutavel.
-- =====================================================================

CREATE TABLE projection_generation (
    id TEXT PRIMARY KEY,
    rule_version_id TEXT NOT NULL REFERENCES rule_version(id),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('building','validated','active','superseded','failed')),
    activated_at TEXT,
    note TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE active_projection_generation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    generation_id TEXT NOT NULL REFERENCES projection_generation(id)
) STRICT;

INSERT INTO projection_generation (id, rule_version_id, created_at, status, activated_at, note)
SELECT
    'legacy-generation-0001',
    (SELECT id FROM rule_version ORDER BY created_at ASC LIMIT 1),
    COALESCE((SELECT MIN(computed_at) FROM competency_state), '1970-01-01T00:00:00+00:00'),
    'active',
    COALESCE((SELECT MIN(computed_at) FROM competency_state), '1970-01-01T00:00:00+00:00'),
    'Geracao legado criada automaticamente pela migration 0002 para linhas existentes antes do modelo de geracoes.'
WHERE EXISTS (SELECT 1 FROM competency_state) AND EXISTS (SELECT 1 FROM rule_version);

CREATE TABLE competency_state_new (
    id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL REFERENCES projection_generation(id),
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

INSERT INTO competency_state_new
    (id, generation_id, competency_id, dimension, state, possible_regression,
     has_unresolved_contradiction, last_evidence_assessment_id, rule_version_id, computed_at, explanation)
SELECT id, 'legacy-generation-0001', competency_id, dimension, state, possible_regression,
       has_unresolved_contradiction, last_evidence_assessment_id, rule_version_id, computed_at, explanation
FROM competency_state;

DROP TABLE competency_state;
ALTER TABLE competency_state_new RENAME TO competency_state;

CREATE INDEX idx_competency_state_lookup ON competency_state (generation_id, competency_id, dimension, computed_at);

CREATE TRIGGER trg_competency_state_immutable_update
BEFORE UPDATE ON competency_state
BEGIN
    SELECT RAISE(ABORT, 'competency_state e append-only: UPDATE proibido');
END;

CREATE TRIGGER trg_competency_state_immutable_delete
BEFORE DELETE ON competency_state
BEGIN
    SELECT RAISE(ABORT, 'competency_state e append-only: DELETE proibido (geracoes antigas sao preservadas)');
END;

INSERT INTO active_projection_generation (id, generation_id)
SELECT 1, 'legacy-generation-0001'
WHERE EXISTS (SELECT 1 FROM projection_generation WHERE id = 'legacy-generation-0001');

-- =====================================================================
-- 8) memory_observation: unico gatilho legitimo para revisao FSRS.
-- =====================================================================

CREATE TABLE memory_observation (
    id TEXT PRIMARY KEY,
    competency_id TEXT NOT NULL REFERENCES competency(id),
    raw_interaction_id TEXT NOT NULL REFERENCES raw_interaction(id),
    evidence_assessment_id TEXT NOT NULL REFERENCES evidence_assessment(id),
    planned_recall INTEGER NOT NULL CHECK (planned_recall IN (0, 1)),
    interval_days REAL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_memory_observation_competency ON memory_observation (competency_id, created_at);
CREATE UNIQUE INDEX idx_memory_observation_raw_interaction ON memory_observation (raw_interaction_id);

CREATE TRIGGER trg_memory_observation_immutable_update
BEFORE UPDATE ON memory_observation
BEGIN
    SELECT RAISE(ABORT, 'memory_observation e imutavel: UPDATE proibido');
END;

CREATE TRIGGER trg_memory_observation_immutable_delete
BEFORE DELETE ON memory_observation
BEGIN
    SELECT RAISE(ABORT, 'memory_observation e imutavel: DELETE proibido');
END;

-- =====================================================================
-- 9) Imutabilidade de decision_event, provider_event, memory_review_log,
--    backup_event (Secao 13 do pacote de correcao v0.2).
-- =====================================================================

CREATE TRIGGER trg_decision_event_immutable_update
BEFORE UPDATE ON decision_event
BEGIN
    SELECT RAISE(ABORT, 'decision_event e imutavel: UPDATE proibido');
END;

CREATE TRIGGER trg_decision_event_immutable_delete
BEFORE DELETE ON decision_event
BEGIN
    SELECT RAISE(ABORT, 'decision_event e imutavel: DELETE proibido');
END;

CREATE TRIGGER trg_provider_event_immutable_update
BEFORE UPDATE ON provider_event
BEGIN
    SELECT RAISE(ABORT, 'provider_event e imutavel: UPDATE proibido');
END;

CREATE TRIGGER trg_provider_event_immutable_delete
BEFORE DELETE ON provider_event
BEGIN
    SELECT RAISE(ABORT, 'provider_event e imutavel: DELETE proibido');
END;

CREATE TRIGGER trg_memory_review_log_immutable_update
BEFORE UPDATE ON memory_review_log
BEGIN
    SELECT RAISE(ABORT, 'memory_review_log e imutavel: UPDATE proibido');
END;

CREATE TRIGGER trg_memory_review_log_immutable_delete
BEFORE DELETE ON memory_review_log
BEGIN
    SELECT RAISE(ABORT, 'memory_review_log e imutavel: DELETE proibido');
END;

CREATE TRIGGER trg_backup_event_immutable_update
BEFORE UPDATE ON backup_event
BEGIN
    SELECT RAISE(ABORT, 'backup_event e imutavel: UPDATE proibido');
END;

CREATE TRIGGER trg_backup_event_immutable_delete
BEFORE DELETE ON backup_event
BEGIN
    SELECT RAISE(ABORT, 'backup_event e imutavel: DELETE proibido');
END;

-- Nota: memory_state NAO e imutavel de proposito - e um ponteiro "card
-- atual" por competencia, atualizado a cada review (documentado em
-- DATA_MODEL.md). O historico completo fica em memory_review_log, que e
-- imutavel. active_rule_version e active_projection_generation tambem sao
-- mutaveis de proposito (sao ponteiros, nao fatos histricos) - ver
-- DECISIONS.md.

COMMIT;
