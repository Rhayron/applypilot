"""Detecção de idioma da vaga, para decidir em que língua entregar o currículo.

Heurística de palavras funcionais em vez de chamada de LLM: distinguir português de
inglês é tarefa fácil e determinística, e uma chamada extra por vaga custaria tempo e
tokens para responder o óbvio. Palavra funcional é o sinal certo porque sobrevive ao
jargão técnico, que é igual nos dois idiomas ("Python", "Docker", "backend").
"""
from __future__ import annotations

import re

# Só marcadores que não colidem entre os dois idiomas. Fora: "a", "e", "no", "em",
# "para" — todos aparecem em texto inglês com tecnologia brasileira no meio, e
# "the/and" aparecem em vaga portuguesa que cita nome de produto.
_PT = {
    "de", "da", "do", "das", "dos", "com", "que", "não", "uma", "um", "você",
    "nós", "sua", "seu", "como", "mais", "sobre", "experiência", "conhecimento",
    "desenvolvimento", "vaga", "empresa", "equipe", "atuar", "será", "são",
    "trabalho", "área", "requisitos", "desejável", "habilidades", "nossa",
}
_EN = {
    "the", "and", "with", "for", "you", "your", "we", "our", "this", "that",
    "have", "will", "are", "is", "of", "to", "in", "on", "as", "experience",
    "skills", "team", "role", "requirements", "responsibilities", "about",
    "years", "strong", "work", "join", "candidate",
}

_PALAVRA = re.compile(r"[a-zà-ú]+", re.IGNORECASE)


def detectar(*textos: str) -> str:
    """Devolve "pt" ou "en" para os textos dados (título, descrição, o que houver).

    Empate ou texto curto demais cai em "pt": é o idioma do currículo base, então o
    fluxo segue sem tradução, que é o caminho mais conservador.
    """
    palavras = _PALAVRA.findall(" ".join(t or "" for t in textos).lower())
    if len(palavras) < 20:
        return "pt"
    pt = sum(1 for p in palavras if p in _PT)
    en = sum(1 for p in palavras if p in _EN)
    return "en" if en > pt else "pt"
