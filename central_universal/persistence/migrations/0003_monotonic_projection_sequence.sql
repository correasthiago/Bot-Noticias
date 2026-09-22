-- Migration 0003: sequencia explicita e monotonica para competency_state
-- (terceira auditoria pos-entrega, pacote de correcao v0.2.1, ponto 1).
--
-- Problema: `CompetencyStateRepository.current()` escolhia a linha "mais
-- recente" por `ORDER BY computed_at DESC, id DESC`. `computed_at` e um
-- timestamp de texto (`utc_now_iso()`); sob escritas rapidas (como o
-- teste `test_P3_two_negatives_from_same_cluster_never_corroborate_regression`,
-- que grava varias avaliacoes em sequencia dentro do mesmo teste) duas
-- linhas podem receber o MESMO `computed_at` se a resolucao do relogio do
-- SO for mais grosseira que o tempo entre duas gravacoes. Quando isso
-- acontece, o desempate caia para `id DESC` - e `id` e um `uuid4().hex`
-- ALEATORIO, entao qual linha "vence" o empate varia de execucao para
-- execucao. Isso produzia uma falha intermitente (a mesma suite passando
-- ou falhando dependendo so da sorte do UUID).
--
-- Correcao: `sequence_number` e um inteiro explicito, atribuido pelo
-- REPOSITORIO (nunca pelo chamador) como `MAX(sequence_number) + 1` na
-- MESMA operacao de escrita que insere a linha - nunca depende de
-- timestamp nem de ordenacao textual de id. `current()`/`history()`
-- passam a ordenar exclusivamente por `sequence_number`.
--
-- O backfill abaixo usa `rowid` (a ordem de insercao FISICA e real do
-- SQLite) como fonte de verdade para reconstruir a sequencia das linhas
-- ja existentes - `competency_state` nunca permite DELETE (trigger de
-- imutabilidade), entao `rowid` jamais foi reciclado e reflete fielmente
-- a ordem de criacao original, ao contrario de `computed_at`.

BEGIN IMMEDIATE;

CREATE TABLE competency_state_new (
    id TEXT PRIMARY KEY,
    sequence_number INTEGER NOT NULL,
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
    (id, sequence_number, generation_id, competency_id, dimension, state, possible_regression,
     has_unresolved_contradiction, last_evidence_assessment_id, rule_version_id, computed_at, explanation)
SELECT
    id,
    ROW_NUMBER() OVER (ORDER BY rowid),
    generation_id, competency_id, dimension, state, possible_regression,
    has_unresolved_contradiction, last_evidence_assessment_id, rule_version_id, computed_at, explanation
FROM competency_state;

DROP TABLE competency_state;
ALTER TABLE competency_state_new RENAME TO competency_state;

CREATE UNIQUE INDEX idx_competency_state_sequence ON competency_state (sequence_number);
CREATE INDEX idx_competency_state_lookup
    ON competency_state (generation_id, competency_id, dimension, sequence_number);

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

COMMIT;
