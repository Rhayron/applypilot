"""Wrapper multi-provider (litellm): Anthropic, OpenAI, Gemini, xAI/Grok, OpenRouter."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

_GEMINI_MAJOR = re.compile(r"gemini-(\d+)")

# O que dizer no system quando não dá para mandar `temperature`. Não é tradução
# exata — é a intenção por trás do número, que é o que o modelo consegue seguir.
_ORIENTACAO_SAMPLING = (
    (0.4, "Priorize precisão, consistência e literalidade. Não varie o estilo nem "
          "explore alternativas criativas: a mesma entrada deve produzir "
          "essencialmente a mesma saída."),
    (0.8, "Equilibre precisão e naturalidade na redação."),
    (float("inf"), "Pode variar formulações e explorar alternativas de redação."),
)


_TRANSITORIOS = ("timeout", "connection", "overloaded", "unavailable",
                 "ratelimit", "rate_limit", "429", "500", "502", "503", "504")


def _transitorio(e: Exception) -> bool:
    """Vale a pena tentar de novo? Timeout e indisponibilidade sim; prompt ruim não."""
    marca = f"{type(e).__name__} {e}".lower()
    return any(t in marca for t in _TRANSITORIOS)


def sampling_via_prompt(model: str) -> bool:
    """O modelo depreciou `temperature`/`top_p`/`top_k`?

    Vale para Gemini 3 em diante: os parâmetros ainda funcionam, mas o Google
    avisa que serão removidos e pede a orientação de sampling no `system`. Mandar
    assim mesmo enche o log de DeprecationWarning a cada chamada e quebra quando a
    remoção acontecer.
    """
    m = _GEMINI_MAJOR.search(model.lower())
    return bool(m) and int(m.group(1)) >= 3


class LLM:
    def __init__(self, model: str, temperature: float = 0.3, timeout: int = 300):
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self._sampling_no_prompt = sampling_via_prompt(model)

    def _orientacao(self) -> str:
        for limite, texto in _ORIENTACAO_SAMPLING:
            if self.temperature < limite:
                return texto
        return _ORIENTACAO_SAMPLING[-1][1]

    def complete(self, system: str, user: str, max_tokens: Optional[int] = None) -> str:
        """Completa. Sem `max_tokens`, o modelo usa o orçamento cheio dele.

        O teto é opcional de propósito. Os modelos Gemini 3+ gastam tokens de
        raciocínio antes de emitir a resposta, e esse consumo sai do mesmo orçamento:
        com um limite apertado o pensamento consome tudo e `content` volta vazio —
        que aqui vira JSON inválido e queima as tentativas do complete_json.
        """
        import litellm  # lazy: permite usar o resto do pacote sem litellm instalado

        litellm.suppress_debug_info = True
        kwargs: dict[str, Any] = {}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if self._sampling_no_prompt:
            system = f"{system}\n\n{self._orientacao()}"
        else:
            kwargs["temperature"] = self.temperature
        # Sem teto de tokens, um pedido grande pode passar do timeout padrão do
        # cliente HTTP. Sem retry aqui, um ReadTimeout transitório derrubava a vaga
        # inteira para FAILED — visto na prática ao adaptar currículo.
        ultimo: Optional[Exception] = None
        for tentativa in range(3):
            try:
                resp = litellm.completion(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    timeout=self.timeout,
                    **kwargs,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001
                if not _transitorio(e):
                    raise
                ultimo = e
                log.warning("Falha transitória no LLM (tentativa %d/3): %s",
                            tentativa + 1, type(e).__name__)
        raise RuntimeError(f"LLM inacessível após 3 tentativas: {ultimo}")

    def complete_json(self, system: str, user: str, max_tokens: Optional[int] = None,
                      retries: int = 2) -> Any:
        """Completa e faz parse de JSON, com retry em caso de saída malformada."""
        system = system + "\n\nResponda APENAS com JSON válido, sem markdown, sem comentários."
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            text = self.complete(system, user, max_tokens=max_tokens)
            try:
                return _extract_json(text)
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("JSON inválido do LLM (tentativa %d): %s", attempt + 1, e)
                user = f"Sua resposta anterior não era JSON válido ({e}). Responda somente o JSON.\n\n{user}"
        raise ValueError(f"LLM não retornou JSON válido: {last_err}")


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # tenta achar o primeiro objeto/array no texto
        m = re.search(r"[\[{].*[\]}]", text, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise
