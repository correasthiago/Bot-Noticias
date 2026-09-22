"""Interface abstrata de fornecedor de IA (Secao 21).

Um Provider sabe gerar (a) um turno de tutor e (b) uma avaliacao bruta.
Ele NUNCA escreve no banco e NUNCA decide estado - apenas produz texto/
payload. Quem grava ProviderEvent, valida o payload do avaliador e aplica
o resultado e a camada de servico (`tutor.service`, `evaluator.service`),
nunca o provider em si.

Trocar de provider e sempre uma escolha explicita de configuracao, nunca
automatica (Principio 17) - por isso o provider ativo e sempre passado
explicitamente para quem monta a orquestracao, nunca descoberto por
fallback silencioso.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from central_universal.evaluator.contract import EvaluatorInput
from central_universal.tutor.contract import TutorInput


@dataclass(frozen=True)
class ProviderCallResult:
    success: bool
    payload: dict | None
    latency_ms: float
    error_message: str | None = None
    tokens_used: int | None = None
    cost: float | None = None


class Provider(ABC):
    provider_name: str
    model: str
    config_version: str

    @abstractmethod
    def generate_tutor_turn(self, tutor_input: TutorInput) -> ProviderCallResult: ...

    @abstractmethod
    def generate_evaluation(self, evaluator_input: EvaluatorInput) -> ProviderCallResult: ...
