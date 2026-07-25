"""Envio de alertas ao Telegram (Bot API via HTTP, síncrono)."""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


def _esc(value) -> str:
    """Escapa para parse_mode=HTML.

    Sem isto, um simples '&' no título ou empresa (R&D, "Data & Analytics") faz a
    Bot API rejeitar a mensagem com 400 e o alerta é perdido silenciosamente.
    """
    return html.escape(str(value or ""), quote=False)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, interactive: bool = False):
        self.token = token
        self.chat_id = chat_id
        # Botão inline só faz sentido se alguém estiver ouvindo o callback. Com o
        # bot próprio desligado quem responde é o Hermes, em linguagem natural —
        # botão aqui viraria clique sem efeito.
        self.interactive = interactive

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send(self, text: str, buttons: Optional[list[list[dict]]] = None) -> None:
        if not self.enabled:
            log.warning("Telegram não configurado; mensagem: %s", text[:200])
            return
        payload: dict = {"chat_id": self.chat_id, "text": text[:4000],
                         "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        try:
            r = httpx.post(self._api("sendMessage"), json=payload, timeout=30)
            r.raise_for_status()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao enviar mensagem Telegram")

    def send_document(self, path: Path, caption: str = "") -> None:
        if not self.enabled or not path or not Path(path).exists():
            return
        try:
            with open(path, "rb") as f:
                r = httpx.post(
                    self._api("sendDocument"),
                    data={"chat_id": self.chat_id, "caption": caption[:1000]},
                    files={"document": (Path(path).name, f)},
                    timeout=60,
                )
            r.raise_for_status()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao enviar documento Telegram")

    # ---- mensagens prontas ----
    def job_alert(self, job_row, resume_file: Optional[Path], mode_review: bool) -> None:
        uid = job_row["uid"]
        text = (
            f"<b>{'🔎 Vaga para revisão' if mode_review else '📋 Vaga encontrada'}</b>\n"
            f"<b>{_esc(job_row['title'])}</b> @ {_esc(job_row['company'])}\n"
            f"📍 {_esc(job_row['location'] or 'n/d')} | Score: <b>{job_row['score']}</b>/100\n"
            f"🔗 {_esc(job_row['url'])}\n\n"
            f"<i>{_esc((job_row['score_reasoning'] or '')[:600])}</i>"
        )
        buttons = None
        if mode_review and self.interactive:
            buttons = [[
                {"text": "✅ Aplicar", "callback_data": f"approve:{uid}"},
                {"text": "❌ Ignorar", "callback_data": f"reject:{uid}"},
            ]]
        elif not self.interactive:
            text += (f"\n\n<code>{_esc(uid)}</code>\n"
                     "💬 Me diga o que fazer: aplicar, descartar ou ver o detalhe.")
        self.send(text, buttons)
        if resume_file:
            self.send_document(Path(resume_file), caption=f"CV adaptado — {job_row['title']}")

    def failure_alert(self, job_row, reason: str, resume_file: Optional[Path]) -> None:
        text = (
            f"⚠️ <b>Não consegui aplicar automaticamente</b>\n"
            f"<b>{_esc(job_row['title'])}</b> @ {_esc(job_row['company'])}\n"
            f"Motivo: {_esc(reason)}\n"
            f"🔗 {_esc(job_row['url'])}\n\n"
            f"Segue o currículo adaptado para você aplicar manualmente. 👇"
        )
        self.send(text)
        if resume_file:
            self.send_document(Path(resume_file), caption=f"CV adaptado — {job_row['title']}")

    def success_alert(self, job_row) -> None:
        self.send(
            f"✅ <b>Aplicação enviada</b>\n"
            f"<b>{_esc(job_row['title'])}</b> @ {_esc(job_row['company'])}\n"
            f"🔗 {_esc(job_row['url'])}"
        )
