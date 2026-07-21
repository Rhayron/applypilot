"""Aplicação automática em vagas Lever (jobs.lever.co/<empresa>/<id>/apply)."""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import Job
from .base import Applier, ApplyResult, has_captcha, launch_page

log = logging.getLogger(__name__)


class LeverApplier(Applier):
    source = "lever"

    def apply(self, job: Job, resume_pdf: Path, cover_letter: str) -> ApplyResult:
        basics = self.answerer.resume.get("basics", {})
        url = job.apply_url or job.url
        if not url.rstrip("/").endswith("/apply"):
            url = url.rstrip("/") + "/apply"

        pw, browser, page = launch_page(self.headless)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            if has_captcha(page):
                return ApplyResult(False, "CAPTCHA no formulário — aplicar manualmente")

            _fill(page, "input[name=name]", basics.get("name", ""))
            _fill(page, "input[name=email]", basics.get("email", ""))
            _fill(page, "input[name=phone]", basics.get("phone", ""))
            for p in basics.get("profiles", []):
                net = (p.get("network") or "").lower()
                if net == "linkedin":
                    _fill(page, "input[name='urls[LinkedIn]']", p.get("url", ""))
                elif net == "github":
                    _fill(page, "input[name='urls[GitHub]']", p.get("url", ""))

            file_input = page.query_selector("input[type=file][name=resume]") \
                or page.query_selector("input[type=file]")
            if not file_input:
                return ApplyResult(False, "campo de upload de currículo não encontrado")
            file_input.set_input_files(str(resume_pdf))
            page.wait_for_timeout(3000)

            if page.query_selector("textarea[name=comments]") and cover_letter:
                page.fill("textarea[name=comments]", cover_letter)

            # cards de perguntas custom obrigatórias
            answered = {}
            for card in page.query_selector_all("li.application-question"):
                label_el = card.query_selector(".application-label")
                if not label_el:
                    continue
                label = (label_el.inner_text() or "").strip()
                required = "✱" in label or "*" in label
                if not required:
                    continue
                select = card.query_selector("select")
                text_in = card.query_selector("input[type=text], textarea")
                radios = card.query_selector_all("input[type=radio]")
                if select:
                    options = [o.inner_text().strip() for o in select.query_selector_all("option")
                               if o.get_attribute("value")]
                    answer, confident = self.answerer.answer(label, options)
                    if not confident or answer not in options:
                        return ApplyResult(False, f"pergunta sem resposta confiável: “{label}”")
                    select.select_option(label=answer)
                    answered[label] = answer
                elif radios:
                    options = [r.get_attribute("value") or "" for r in radios]
                    answer, confident = self.answerer.answer(label, options)
                    if not confident or answer not in options:
                        return ApplyResult(False, f"pergunta sem resposta confiável: “{label}”")
                    for r in radios:
                        if (r.get_attribute("value") or "") == answer:
                            r.check()
                    answered[label] = answer
                elif text_in and not (text_in.input_value() or "").strip():
                    answer, confident = self.answerer.answer(label)
                    if not confident:
                        return ApplyResult(False, f"pergunta sem resposta confiável: “{label}”")
                    text_in.fill(answer)
                    answered[label] = answer

            if has_captcha(page):
                return ApplyResult(False, "CAPTCHA antes do envio — aplicar manualmente")

            btn = page.query_selector("button#btn-submit, button[type=submit]")
            if not btn:
                return ApplyResult(False, "botão de envio não encontrado")
            btn.click()
            page.wait_for_timeout(5000)

            content = page.content().lower()
            if any(s in content for s in ("thank", "application has been", "submitted", "obrigado")):
                return ApplyResult(True, "aplicado com sucesso", answered=answered)
            return ApplyResult(False, "confirmação de envio não detectada", answered=answered)
        except Exception as e:  # noqa: BLE001
            log.exception("Erro aplicando em %s", job.short())
            return ApplyResult(False, f"erro: {e}")
        finally:
            browser.close()
            pw.stop()


def _fill(page, selector: str, value: str):
    if value and page.query_selector(selector):
        page.fill(selector, value)
