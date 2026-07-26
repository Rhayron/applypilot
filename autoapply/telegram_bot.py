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

    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ajuda", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("metrics", cmd_metrics))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("filtros", cmd_filtros))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Bot Telegram iniciado")
    app.run_polling()


AJUDA = """🤖 <b>AutoPilot</b> — busca vagas, adapta seu currículo e candidata.

<b>Como funciona</b>
A cada {intervalo} min eu procuro vagas e pontuo cada uma. As que passam do corte
viram uma mensagem com botões:
  [🤖 Claude] [⚡ Gemini] — gera o CV adaptado com esse editor
  [❌ Descartar] — some da fila
Depois do CV pronto, se a fonte aceitar envio automático, aparecem
  [✅ Aplicar] [❌ Ignorar]
Nada é gerado nem enviado sem você clicar.

<b>Comandos</b>
/status — quantas vagas em cada estágio, aplicações de hoje
/pending — reenvia a fila com os botões de cada uma
/metrics — histórico dos ciclos
/filtros — ver e mudar o que eu procuro (veja abaixo)
/help — esta mensagem

<b>Enviar um link</b>
Cole a URL de uma vaga e eu adapto seu currículo para ela na hora.

<b>/filtros</b>
Sem argumento, mostra os filtros atuais. Para mudar:

<code>/filtros local Curitiba, São Paulo</code>
<code>/filtros local -</code>  (aceita de qualquer lugar)
<code>/filtros +titulo Firmware Engineer</code>
<code>/filtros -titulo Fullstack Developer</code>
<code>/filtros +palavra rtos, microcontrolador</code>
<code>/filtros -palavra angular</code>
<code>/filtros modalidade remoto</code>      só vaga remota
<code>/filtros modalidade presencial</code>  só vaga presencial
<code>/filtros modalidade ambos</code>       híbrido: aceita as duas
<code>/filtros dias 14</code>     (idade máxima da vaga)
<code>/filtros intervalo 120</code> (minutos entre ciclos)
<code>/filtros corte 70</code>    (nota mínima para me avisar)

<b>titulo x palavra</b> — a diferença importa:
• <b>titulo</b> casa só no nome do cargo. Use o que aparece no título do anúncio.
• <b>palavra</b> casa no título <i>ou</i> na descrição. Use termo de nicho que
  costuma estar no corpo, como "firmware embarcado" ou "visão computacional".
Uma vaga entra se bate um titulo <i>ou</i> uma palavra.

<b>Modalidade + local</b> — combinam assim:
• <code>remoto</code> + local → poucas vagas: anúncio remoto raramente cita cidade.
• <code>presencial</code> + local → o uso natural de filtrar por cidade.
• <code>ambos</code> + local → pega presencial na cidade e remoto que a mencione.
Vaga que não informa a modalidade sempre passa, porque a maioria das fontes omite
e descartar por omissão jogaria fora quase tudo.

Mudança vale no próximo ciclo e não reavalia vaga já vista."""


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = ctx.bot_data["orch"]
    await update.message.reply_text(
        AJUDA.format(intervalo=orch.cfg.search.interval_minutes), parse_mode="HTML")


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
    """Reenvia a fila inteira, cada vaga com os botões do estágio em que está."""
    from pathlib import Path

    orch: Orchestrator = ctx.bot_data["orch"]
    rows = orch.tracker.awaiting_decision()
    if not rows:
        await update.message.reply_text("Nenhuma vaga aguardando decisão.")
        return
    for row in rows[:10]:
        if row["status"] == "pending_generation":
            orch.notifier.vaga_encontrada(row)
        else:
            arquivo = Path(row["pdf_path"]) if row["pdf_path"] else None
            orch.notifier.job_alert(row, arquivo,
                                    mode_review=row["status"] == "pending_review")
    await update.message.reply_text(f"{len(rows)} vaga(s) na fila.")


def _lista(texto: str) -> list[str]:
    """'Curitiba, São Paulo' -> ['Curitiba', 'São Paulo']."""
    return [x.strip() for x in texto.split(",") if x.strip()]


def _resumo_filtros(s) -> str:
    return (
        f"🔎 <b>Filtros atuais</b>\n\n"
        f"<b>Títulos</b> ({len(s.titles)}): {', '.join(s.titles) or '—'}\n\n"
        f"<b>Palavras</b> ({len(s.keywords)}): {', '.join(s.keywords) or '—'}\n\n"
        f"<b>Locais</b>: {', '.join(s.locations) or 'qualquer lugar'}\n"
        f"<b>Modalidade</b>: {s.modo.value}"
        f"{' (remoto + presencial)' if s.modo.value == 'ambos' else ''}\n"
        f"<b>Idade máxima</b>: {s.max_age_days} dias\n"
        f"<b>Intervalo</b>: {s.interval_minutes} min\n\n"
        f"<i>/help mostra como mudar cada um.</i>"
    )


async def cmd_filtros(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra e ajusta os filtros de busca sem sair do chat."""
    from .config import aplicar_mudancas

    orch: Orchestrator = ctx.bot_data["orch"]
    args = (ctx.args or [])
    if not args:
        await update.message.reply_text(_resumo_filtros(orch.cfg.search),
                                        parse_mode="HTML")
        return

    acao, valor = args[0].lower(), " ".join(args[1:]).strip()
    s = orch.cfg.search
    mudancas: dict = {}
    erro = None

    if acao == "local":
        mudancas["search.locations"] = [] if valor in ("-", "") else _lista(valor)
    elif acao in ("+titulo", "+título"):
        atuais = {t.lower() for t in s.titles}
        mudancas["search.titles"] = s.titles + [
            v for v in _lista(valor) if v.lower() not in atuais]
    elif acao in ("-titulo", "-título"):
        fora = {v.lower() for v in _lista(valor)}
        mudancas["search.titles"] = [t for t in s.titles if t.lower() not in fora]
    elif acao == "+palavra":
        atuais = {k.lower() for k in s.keywords}
        mudancas["search.keywords"] = s.keywords + [
            v for v in _lista(valor) if v.lower() not in atuais]
    elif acao == "-palavra":
        fora = {v.lower() for v in _lista(valor)}
        mudancas["search.keywords"] = [k for k in s.keywords if k.lower() not in fora]
    elif acao in ("modalidade", "modo", "remoto"):
        v = valor.lower()
        # "remoto on/off" continua valendo: era a forma antiga e vira modalidade.
        equivalente = {"on": "remoto", "off": "ambos", "sim": "remoto", "nao": "ambos",
                       "não": "ambos", "hibrido": "ambos", "híbrido": "ambos",
                       "todos": "ambos", "qualquer": "ambos"}
        v = equivalente.get(v, v)
        if v not in ("remoto", "presencial", "ambos"):
            erro = ("use <code>/filtros modalidade remoto</code>, "
                    "<code>presencial</code> ou <code>ambos</code>")
        else:
            mudancas["search.modalidade"] = v
            # Mantém o flag antigo coerente para quem ler o config na mão.
            mudancas["search.remote_only"] = v == "remoto"
    elif acao in ("dias", "intervalo", "corte"):
        campo = {"dias": "search.max_age_days",
                 "intervalo": "search.interval_minutes",
                 "corte": "matching.alert_threshold"}[acao]
        try:
            mudancas[campo] = int(valor)
        except ValueError:
            erro = f"<code>/filtros {acao}</code> espera um número"
    else:
        erro = f"não conheço <code>{acao}</code>. Veja /help"

    if erro:
        await update.message.reply_text(f"⚠️ {erro}", parse_mode="HTML")
        return

    r = await asyncio.to_thread(aplicar_mudancas,
                                orch.cfg.base_dir / "config.yaml", mudancas)
    if r.get("erro"):
        await update.message.reply_text(f"⚠️ {r['erro']}")
        return
    # Recarrega já, para o /filtros seguinte mostrar o valor novo.
    await asyncio.to_thread(orch.reload_config)
    await update.message.reply_text(
        "✅ Atualizado. Vale no próximo ciclo.\n\n" + _resumo_filtros(orch.cfg.search),
        parse_mode="HTML")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Trata os dois portões de decisão do fluxo.

    Portão 1 — gen_claude / gen_gemini / reject: gerar ou não o currículo.
    Portão 2 — approve / reject: enviar ou não a candidatura.
    """
    orch: Orchestrator = ctx.bot_data["orch"]
    query = update.callback_query
    await query.answer()
    action, _, uid = query.data.partition(":")
    row = orch.tracker.get(uid)
    if not row:
        await query.edit_message_text("Vaga não encontrada no tracker.")
        return

    vaga = f"{row['title']} @ {row['company']}"

    if action == "reject":
        orch.tracker.set_status(uid, ApplicationStatus.REJECTED_BY_USER)
        await query.edit_message_text(f"❌ Descartada: {vaga}")

    elif action in ("gen_claude", "gen_gemini"):
        editor = "claude" if action == "gen_claude" else "gemini"
        # Trocar o teclado antes de começar evita clique duplo: gerar leva ~1 min e
        # o botão continuaria clicável, disparando uma segunda adaptação paga.
        await query.edit_message_text(
            f"⏳ Gerando com <b>{editor}</b>: {vaga}\n"
            f"<i>leva de 30s a 2min…</i>", parse_mode="HTML")
        try:
            arquivo = await asyncio.to_thread(orch.gerar_cv, uid, editor)
            if not arquivo:
                await query.edit_message_text(f"⚠️ Não consegui gerar: {vaga}")
        except Exception as e:  # noqa: BLE001
            log.exception("Falha ao gerar CV de %s", uid)
            await query.edit_message_text(f"⚠️ Falhou ({type(e).__name__}): {vaga}")

    elif action == "approve":
        await query.edit_message_text(f"⏳ Aplicando em {vaga}…")
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
