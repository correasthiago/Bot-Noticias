"""Fonte unica de tempo do sistema.

Principio: "Datas devem ser armazenadas de maneira inequivoca; apresentacao
converte para horario local" (Secao 6). Armazenamos sempre UTC ISO-8601
com offset explicito; a camada web converte para horario local do
navegador (via JS) apenas na apresentacao.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
