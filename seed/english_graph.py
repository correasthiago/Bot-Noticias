"""Seed inicial do dominio de ingles (Secao 19).

O grafo modela pre-requisitos EXPLICITAMENTE - nunca presuma que uma
competencia precisa ser estudada so porque aparece "cedo" nesta lista
(Secao 18). A ordem aqui e so para leitura humana; quem decide o que
estudar e sempre o Decisor, a partir de evidencia real.
"""

from __future__ import annotations

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Competency, LearningDomain, PrerequisiteRelation
from central_universal.domain.ids import new_id
from central_universal.persistence.repositories import Repositories

DOMAIN_CODE = "english"
DOMAIN_NAME = "Ingles"

# (code, nome, descricao)
COMPETENCIES: tuple[tuple[str, str, str], ...] = (
    ("be", "BE (Simple Present)", "am/is/are em afirmativas, negativas e perguntas simples."),
    ("have", "HAVE (Simple Present)", "have/has para posse e usos basicos."),
    ("simple_present", "Simple Present (afirmativo)", "Verbos regulares no presente simples, incluindo -s na 3a pessoa."),
    ("do_does", "DO/DOES (auxiliar)", "DO/DOES como auxiliar no presente simples."),
    ("simple_present_qn", "Simple Present - perguntas e negacoes", "Perguntas e negacoes no presente simples usando DO/DOES."),
    ("simple_past_regular", "Simple Past (verbos regulares)", "Passado simples com verbos regulares (-ed)."),
    ("simple_past_irregular", "Simple Past (verbos irregulares)", "Passado simples com verbos irregulares comuns."),
    ("be_past", "BE (Simple Past)", "was/were em afirmativas, negativas e perguntas."),
    ("did", "DID (auxiliar)", "DID como auxiliar no passado simples."),
    ("simple_past_qn", "Simple Past - perguntas e negacoes", "Perguntas e negacoes no passado simples usando DID."),
    ("present_perfect_basic", "Present Perfect (basico)", "have/has + particípio para experiencia e resultado presente."),
    ("simple_past_vs_present_perfect", "Simple Past x Present Perfect", "Contraste de uso entre passado simples e present perfect."),
    ("will", "WILL (futuro)", "WILL para previsao/decisao espontanea."),
    ("going_to", "GOING TO (futuro)", "BE going to para planos e previsoes com evidencia."),
    ("could", "COULD", "COULD para habilidade passada e pedidos educados."),
    ("should", "SHOULD", "SHOULD para conselho e recomendacao."),
    ("would", "WOULD", "WOULD para hipoteses, pedidos educados e habitos passados."),
)

# (competency_code, prerequisite_code)
PREREQUISITES: tuple[tuple[str, str], ...] = (
    ("simple_present", "be"),
    ("do_does", "simple_present"),
    ("simple_present_qn", "do_does"),
    ("simple_past_regular", "simple_present"),
    ("simple_past_irregular", "simple_past_regular"),
    ("be_past", "be"),
    ("did", "simple_past_regular"),
    ("did", "simple_past_irregular"),
    ("simple_past_qn", "did"),
    ("present_perfect_basic", "simple_past_regular"),
    ("present_perfect_basic", "simple_past_irregular"),
    ("present_perfect_basic", "be_past"),
    ("simple_past_vs_present_perfect", "present_perfect_basic"),
    ("simple_past_vs_present_perfect", "simple_past_qn"),
    ("will", "simple_present"),
    ("going_to", "simple_present"),
    ("could", "simple_past_irregular"),
    ("should", "simple_present"),
    ("would", "will"),
)


def seed(repos: Repositories) -> bool:
    """Idempotente: nao faz nada se o dominio 'english' ja existir.
    Retorna True se semeou agora, False se ja existia."""

    existing = repos.domains.get_by_code(DOMAIN_CODE)
    if existing is not None:
        return False

    domain = LearningDomain(id=new_id(), code=DOMAIN_CODE, name=DOMAIN_NAME)
    repos.domains.insert(domain)

    code_to_id: dict[str, str] = {}
    for code, name, description in COMPETENCIES:
        competency = Competency(
            id=new_id(), domain_id=domain.id, code=code, name=name, description=description
        )
        repos.competencies.insert(competency)
        code_to_id[code] = competency.id

    now = utc_now_iso()
    for competency_code, prerequisite_code in PREREQUISITES:
        repos.prerequisites.insert(
            PrerequisiteRelation(
                id=new_id(),
                competency_id=code_to_id[competency_code],
                prerequisite_id=code_to_id[prerequisite_code],
                created_at=now,
            )
        )

    return True
