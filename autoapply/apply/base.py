"""Base do agente de aplicação (Playwright) + respostas de screening via LLM.

Princípios:
  * Nada de burlar CAPTCHA nem esconder automação — se houver CAPTCHA ou
    campo obrigatório sem resposta confiável, a aplicação FALHA e vira alerta
    no Telegram com o CV pronto (human-in-the-loop).
"""
from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..llm import LLM
from ..models import Job

log = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    success: bool
    reason: str = ""
    screenshot: Optional[Path] = None
    answered: dict = field(default_factory=dict)


SCREENING_SYSTEM = """Você responde perguntas de formulários de candidatura em nome do candidato.
Use SOMENTE o banco de respostas, o currículo e o contexto fornecidos — nunca invente fatos.
Para cada pergunta retorne: {"answer": "...", "confident": true|false}
confident=false quando não houver informação suficiente (ex.: pergunta sobre salário sem dado,
pergunta jurídica, essay question específica). Nesses casos a aplicação irá para revisão humana.
Responda no idioma da pergunta. Para múltipla escolha, escolha exatamente uma das opções dadas."""


class ScreeningAnswerer:
    def __init__(self, llm: LLM, resume: dict, context: str, answers_bank: dict):
        self.llm = llm
        self.resume = resume
        self.context = context
        self.bank = answers_bank

    def answer(self, question: str, options: list[str] | None = None) -> tuple[str, bool]:
        user = f"""Pergunta do formulário: {question}
{f"Opções (escolha uma): {options}" if options else "(campo de texto livre)"}

Banco de respostas do candidato:
{json.dumps(self.bank, ensure_ascii=False)}

Currículo (resumo):
{json.dumps(self.resume.get("basics", {}), ensure_ascii=False)}
Skills: {json.dumps(self.resume.get("skills", []), ensure_ascii=False)}

Contexto:
{self.context[:2500]}"""
        try:
            data = self.llm.complete_json(SCREENING_SYSTEM, user)
            return str(data.get("answer", "")), bool(data.get("confident", False))
        except Exception:  # noqa: BLE001
            log.exception("Falha ao responder screening: %s", question)
            return "", False


class Applier(abc.ABC):
    """Aplica automaticamente em um tipo de ATS."""

    source: str = "base"

    def __init__(self, answerer: ScreeningAnswerer, headless: bool = True):
        self.answerer = answerer
        self.headless = headless

    @abc.abstractmethod
    def apply(self, job: Job, resume_pdf: Path, cover_letter: str) -> ApplyResult: ...


def launch_page(headless: bool = True):
    """Contexto Playwright simples (sem stealth — automação transparente)."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    page = browser.new_page()
    return pw, browser, page


CAPTCHA_MARKERS = ["recaptcha", "hcaptcha", "cf-turnstile", "captcha"]


def has_captcha(page) -> bool:
    content = page.content().lower()
    return any(m in content for m in CAPTCHA_MARKERS)
