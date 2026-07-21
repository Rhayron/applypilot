"""Aplicação automática em boards Greenhouse (boards.greenhouse.io)."""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import Job
from .base import Applier, ApplyResult, has_captcha, launch_page

log = logging.getLogger(__name__)


class GreenhouseApplier(Applier):
    source = "greenhouse"

    def apply(self, job: Job, resume_pdf: Path, cover_letter: str) -> ApplyResult:
        basics = self.answerer.resume.get("basics", {})
        name_parts = (basics.get("name") or "").split(" ", 1)
        first, last = name_parts[0], (name_parts[1] if len(name_parts) > 1 else "-")

        pw, browser, page = launch_page(self.headless)
        try:
            page.goto(job.apply_url or job.url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            if has_captcha(page):
                return ApplyResult(False, "CAPTCHA no formulário — aplicar manualmente")

            # campos padrão do Greenhouse
            _fill(page, "#first_name", first)
            _fill(page, "#last_name", last)
            _fill(page, "#email", basics.get("email", ""))
            _fill(page, "#phone", basics.get("phone", ""))

            # upload do currículo
            file_input = page.query_selector("input[type=file]")
            if file_input:
                file_input.set_input_files(str(resume_pdf))
                page.wait_for_timeout(3000)
            else:
                return ApplyResult(False, "campo de upload de currículo não encontrado")

            # cover letter (textarea, se existir)
            for sel in ("#cover_letter_text", "textarea[name*=cover]"):
                if page.query_selector(sel):
                    page.fill(sel, cover_letter)
                    break

            # perguntas custom obrigatórias
            answered = {}
            ok, reason = self._answer_customs(page, answered)
            if not ok:
                return ApplyResult(False, reason, answered=answered)

            if has_captcha(page):
                return ApplyResult(False, "CAPTCHA antes do envio — aplicar manualmente")

            submit = page.query_selector("#submit_app, button[type=submit]")
            if not submit:
                return ApplyResult(False, "botão de envio não encontrado")
            submit.click()
            page.wait_for_timeout(5000)

            content = page.content().lower()
            if any(s in content for s in ("thank", "obrigado", "application has been", "recebemos")):
                return ApplyResult(True, "aplicado com sucesso", answered=answered)
            return ApplyResult(False, "confirmação de envio não detectada", answered=answered)
        except Exception as e:  # noqa: BLE001
            log.exception("Erro aplicando em %s", job.short())
            return ApplyResult(False, f"erro: {e}")
        finally:
            browser.close()
            pw.stop()

    def _answer_customs(self, page, answered: dict) -> tuple[bool, str]:
        """Responde perguntas custom (select/text) obrigatórias via LLM."""
        for field in page.query_selector_all("div[class*=field], .application-question"):
            label_el = field.query_selector("label")
            if not label_el:
                continue
            label = (label_el.inner_text() or "").strip()
            required = "*" in label
            if not label or not required:
                continue

            select = field.query_selector("select")
            text_in = field.query_selector("input[type=text], textarea")
            if select:
                options = [o.inner_text().strip() for o in select.query_selector_all("option")
                           if o.get_attribute("value")]
                answer, confident = self.answerer.answer(label, options)
                if not confident or answer not in options:
                    return False, f"pergunta sem resposta confiável: “{label}”"
                select.select_option(label=answer)
                answered[label] = answer
            elif text_in and not (text_in.input_value() or "").strip():
                answer, confident = self.answerer.answer(label)
                if not confident:
                    return False, f"pergunta sem resposta confiável: “{label}”"
                text_in.fill(answer)
                answered[label] = answer
        return True, ""


def _fill(page, selector: str, value: str):
    if value and page.query_selector(selector):
        page.fill(selector, value)
