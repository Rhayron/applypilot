"""Wrapper multi-provider (litellm): Anthropic, OpenAI, Gemini, xAI/Grok, OpenRouter."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)


class LLM:
    def __init__(self, model: str, temperature: float = 0.3):
        self.model = model
        self.temperature = temperature

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
        resp = litellm.completion(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

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
