# Constituicao Pedagogica — Central Universal V0.1

Estes 20 principios nao podem ser alterados por conveniencia de
implementacao. Qualquer mudanca de regra pedagogica exige uma nova
`RuleVersion` (Principio 18), nunca uma edicao silenciosa do
comportamento existente. Cada principio abaixo aponta para onde ele e
imposto no codigo e qual teste do Red Team o exercita.

1. **Aula concluida nao significa competencia adquirida.**
   O Tutor (`tutor/`) nunca escreve em `CompetencyState`; so o motor de
   agregacao (`evidence/aggregation.py`), a partir de evidencia real, o
   faz.

2. **Probabilidade nao declara dominio sozinha.**
   `CompetencyDimensionState` nao e um score numerico de "mastery": e uma
   maquina de estados baseada em contagem de evidencia independente
   (ver DECISIONS.md).

3. **Estado atual nao substitui historico.**
   `competency_state` e append-only (Secao 14); `raw_interaction`,
   `evidence_event` e `evidence_assessment` nunca sao apagados ou
   sobrescritos.

4. **Evidencias brutas sao imutaveis.**
   Triggers SQL (`trg_raw_interaction_immutable_*`,
   `trg_evidence_event_immutable_*`) bloqueiam UPDATE/DELETE no nivel do
   banco, nao so por convencao de codigo. Verificado por
   `check_immutability_triggers_present` e por
   `test_raw_interaction_is_immutable`.

5. **Avaliacoes sao versionadas.**
   Toda `EvidenceAssessment` carrega `rule_version_id`; mudar de regra
   cria uma nova avaliacao, nunca edita a antiga (T11).

6. **Estados derivados devem ser recalculaveis.**
   `recompute_all_from_log` reconstroi `competency_state` inteiro a
   partir de `raw_interaction + evidence_event + evidence_assessment +
   rule_version` (T10, `check_state_matches_recomputation`).

7. **Professor nao certifica aquilo que acabou de ensinar.**
   `tutor.contract.TutorOutput` nao tem nenhum campo de estado; o Tutor
   so produz texto de atividade.

8. **Professor, Avaliador e Decisor sao funcoes separadas.**
   Tres modulos (`tutor/`, `evaluator/`, `decision/`) com contratos
   proprios; o Decisor (`decision/engine.py`) nao chama nenhum provider.

9. **O Decisor V0 deve ser deterministico/auditavel.**
   `decision/engine.py` e codigo puro, sem I/O, sem chamada de LLM;
   cada saida carrega `rule_applied` (slug estavel) e `justification`
   (texto).

10. **Repeticoes no mesmo dia nao provam retencao longitudinal.**
    `classify_dimension` exige dias DISTINTOS de evidencia forte para
    `retention` chegar a `demonstrated`/`consolidated` (T1, T12).

11. **Evidencia com ajuda nao equivale a recuperacao independente.**
    Evidencia positiva em A2/A3 nunca conta como `strong_positive` em
    `classify_dimension` (T2, `check_retrieval_independence`).

12. **Um erro isolado nao apaga dominio anterior.**
    `possible_regression` so vira rebaixamento de estado quando
    corroborado por >=2 evidencias negativas/contraditorias recentes
    (T3).

13. **Evidencia contraditoria deve poder coexistir.**
    `EvidenceType.CONTRADICTORY` e um tipo de primeira classe; o estado
    vai para `insufficient_evidence` (incerteza), nunca e descartado
    (T6).

14. **Incerteza e um estado legitimo.**
    `insufficient_evidence` e `inconclusive` sao valores validos, nao
    erros; `rating_from_production_result` deliberadamente nao atribui
    nota de memoria a producoes `inconclusive`.

15. **A coleta de evidencia deve ser subordinada a aprendizagem.**
    O Decisor escolhe a atividade pedagogica (Secao 17) antes de
    qualquer preocupacao de "quanto dado coletar"; o MockProvider nunca
    otimiza para maximizar volume de evidencia.

16. **Nenhum fornecedor de IA deve ser estruturalmente indispensavel.**
    `providers/base.py` define uma interface abstrata; `MockProvider`
    prova que o motor inteiro roda sem nenhum fornecedor real (T15).

17. **Nenhuma mudanca de fornecedor, infraestrutura ou componente pago
    pode ocorrer automaticamente.**
    `web/deps.py` define o provider ativo como uma unica linha de
    configuracao explicita — nunca ha fallback automatico entre
    providers.

18. **Mudanca de regra pedagogica cria nova versao.**
    `RuleVersion` e imutavel uma vez criada; `evidence_assessment` e
    `competency_state` sempre referenciam a versao vigente no momento do
    calculo (T11).

19. **Falha de componente externo nao pode corromper estado valido.**
    Falha do Tutor, do Avaliador, do FSRS ou do backup nunca apaga ou
    corrompe evidencia ja gravada (Secao 25; T7, T9, T14; testes de
    `test_backup.py`).

20. **O sistema deve explicar por que tomou uma decisao.**
    Todo `DecisionEvent` tem `justification` obrigatoria e nao vazia,
    reforcada por CHECK constraint no banco
    (`check_decision_events_without_justification`).
