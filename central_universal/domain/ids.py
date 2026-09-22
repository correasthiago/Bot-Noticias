"""Geracao de identificadores opacos.

Requisito: "IDs nao devem depender de nomes humanos mutaveis" (Secao 6).
Usamos uuid4 hexadecimal: opaco, estavel, sem qualquer dependencia de
nome, ordem de insercao ou dado mutavel.
"""

from __future__ import annotations

import uuid


def new_id(prefix: str = "") -> str:
    token = uuid.uuid4().hex
    return f"{prefix}{token}" if prefix else token
