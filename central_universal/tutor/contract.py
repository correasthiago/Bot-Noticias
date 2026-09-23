"""Contrato de entrada/saida do Tutor (Secao 15).

O Tutor recebe objetivo, competencias-alvo, contexto, nivel de suporte
permitido e restricoes pedagogicas; conduz a interacao com o aprendiz.
Ele NUNCA tem acesso de escrita a CompetencyState - so pode produzir
texto/atividade, nunca uma decisao de estado (Principio 7: "professor nao
certifica aquilo que acabou de ensinar"; Principio 8: papeis separados).
"""

from __future__ import annotations

from dataclasses import dataclass

from central_universal.domain.enums import HelpLevel


@dataclass(frozen=True)
class TutorInput:
    objective: str
    target_competency_ids: tuple[str, ...]
    context: str
    allowed_support_level: HelpLevel
    pedagogical_constraints: tuple[str, ...] = ()
    prompt_seed: str = ""


@dataclass(frozen=True)
class TutorOutput:
    utterance: str
    activity_prompt: str
    provider_event_id: str | None
