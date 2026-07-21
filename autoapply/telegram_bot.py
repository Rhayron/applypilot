"""Bot Telegram interativo: aprovação de aplicações + tailoring on-demand.

Comandos:
  /start   — instruções
  /status  — estatísticas do tracker
  /metrics — histórico de métricas por ciclo de busca
  /pending — vagas aguardando aprovação
  (enviar um link de vaga) — adapta o CV na hora e devolve o arquivo
  Botões ✅/❌ — aprova/rejeita aplicações em modo review
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from .models import ApplicationStatus
from .orchestrator import Orchestrator

log = logging.getLogger(__name__)


def run_bot(orch: Orchestrator) -> None:
    token = orch.cfg.telegram.token
    if not token:
        raise SystemExit("Defina TELEGRAM_BOT_TOKEN no .env")

    app = Application.builder().token(token).build()
    app.bot_data["orch"] = orch

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("metrics", cmd_metrics))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Bot Telegram iniciado")
    app.run_polling()


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 AutoApply\n\n"
        "• Envie o link de uma vaga e eu devolvo seu CV adaptado.\n"
        "• /status — estatísticas\n"
        "• /pending — vagas aguardando sua aprovação\n"
        f"• Seu chat_id é {update.effective_chat.id} (coloque em TELEGRAM_CHAT_ID)"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = ctx.bot_data["orch"]
    stats = orch.tracker.stats()
    lines = [f"{k}: {v}" for k, v in sorted(stats.items())] or ["nada ainda"]
    await update.message.reply_text("📊 Status\n" + "\n".join(lines))


async def cmd_metrics(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = ctx.bot_data["orch"]
    totals = orch.tracker.cycle_totals()
    runs = totals.get("runs", 0)
    if not runs:
        await update.message.reply_text("Nenhum ciclo registrado ainda.")
        return
    lines = [f"📈 Métricas ({runs} ciclos, último: {totals['last_run']})", "", "Totais:"]
    lines += [f"• {k}: {totals[k]}" for k in
              ("discovered", "new", "tailored", "applied", "alerted", "failed", "skipped")]
    recent = orch.tracker.cycle_history(5)
    if recent:
        lines += ["", "Últimos ciclos:"]
        lines += [f"• {r['ran_at']} — disc {r['discovered']}, appl {r['applied']}, "
                  f"alert {r['alerted']}, skip {r['skipped']}" for r in recent]
    await update.message.reply_text("\n".join(lines))


async def cmd_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = ctx.bot_data["orch"]
    rows = orch.tracker.pending_review()
    if not rows:
        await update.message.reply_text("Nenhuma vaga aguardando aprovação.")
        return
    for row in rows[:10]:
        orch.notifier.job_alert(row, row["pdf_path"], mode_review=True)
    await update.message.reply_text(f"{len(rows)} vaga(s) pendente(s) reenviada(s).")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = ctx.bot_data["orch"]
    query = update.callback_query
    await query.answer()
    action, _, uid = query.data.partition(":")
    row = orch.tracker.get(uid)
    if not row:
        await query.edit_message_text("Vaga não encontrada no tracker.")
        return
    if action == "reject":
        orch.tracker.set_status(uid, ApplicationStatus.REJECTED_BY_USER)
        await query.edit_message_text(f"❌ Ignorada: {row['title']} @ {row['company']}")
    elif action == "approve":
        await query.edit_message_text(
            f"⏳ Aplicando em {row['title']} @ {row['company']}..."
        )
        ok = await asyncio.to_thread(orch.apply_by_uid, uid)
        # resultado detalhado já é enviado pelo notifier
        if not ok:
            log.info("Aplicação aprovada falhou para %s", uid)


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = ctx.bot_data["orch"]
    text = (update.message.text or "").strip()
    if not text.startswith("http"):
        await update.message.reply_text("Envie o link de uma vaga para eu adaptar seu CV.")
        return
    await update.message.reply_text("⏳ Adaptando seu currículo para essa vaga...")
    try:
        summary, path = await asyncio.to_thread(orch.tailor_url, text)
        await update.message.reply_text(summary[:4000])
        if path:
            with open(path, "rb") as f:
                await update.message.reply_document(f, filename=path.name)
    except Exception as e:  # noqa: BLE001
        log.exception("Falha no tailoring on-demand")
        await update.message.reply_text(f"⚠️ Falhou: {e}")
