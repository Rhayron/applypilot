"""CLI do AutoApply.

Uso:
  autoapply once                 # roda um ciclo de busca/aplicação agora
  autoapply run                  # scheduler: ciclo a cada N min + bot Telegram
  autoapply bot                  # só o bot Telegram (aprovações + tailoring on-demand)
  autoapply tailor <url>         # adapta o CV para uma vaga específica
  autoapply status               # estatísticas do tracker (vagas por status)
  autoapply metrics              # histórico de métricas por ciclo de busca
  autoapply mcp                  # servidor MCP (stdio) — é por aqui que o Hermes controla
  autoapply -c outro/config.yaml once
"""
from __future__ import annotations

import argparse
import logging
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("autoapply")


def main() -> None:
    parser = argparse.ArgumentParser(prog="autoapply")
    parser.add_argument("-c", "--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("once", help="um ciclo de busca/aplicação")
    sub.add_parser("run", help="scheduler + bot Telegram")
    sub.add_parser("bot", help="somente o bot Telegram")
    p_tailor = sub.add_parser("tailor", help="adapta CV para uma URL")
    p_tailor.add_argument("url")
    sub.add_parser("status", help="estatísticas")
    sub.add_parser("metrics", help="histórico de métricas por ciclo")
    sub.add_parser("mcp", help="servidor MCP via stdio (usado pelo Hermes)")
    sub.add_parser("pending", help="vagas aguardando decisão, em JSON")
    args = parser.parse_args()

    # O MCP fala o protocolo por stdout: precisa sair antes que qualquer outra coisa
    # (logs de boot, avisos do litellm) suje o canal.
    if args.cmd == "mcp":
        from .mcp_server import run as run_mcp
        run_mcp(args.config)
        return

    from .config import load_config
    from .orchestrator import Orchestrator

    cfg = load_config(args.config)
    orch = Orchestrator(cfg)

    if args.cmd == "once":
        stats = orch.run_cycle()
        print(stats)

    elif args.cmd == "status":
        for k, v in sorted(orch.tracker.stats().items()):
            print(f"{k:20s} {v}")

    elif args.cmd == "metrics":
        totals = orch.tracker.cycle_totals()
        runs = totals.get("runs", 0)
        if not runs:
            print("Nenhum ciclo registrado ainda.")
        else:
            print(f"Ciclos executados: {runs}  (último: {totals['last_run']})")
            print("Totais acumulados:")
            for k in ("discovered", "new", "tailored", "applied",
                      "alerted", "failed", "skipped"):
                print(f"  {k:12s} {totals[k]}")
            print("\nÚltimos ciclos:")
            for r in orch.tracker.cycle_history(10):
                print(f"  {r['ran_at']}  disc={r['discovered']} new={r['new']} "
                      f"tail={r['tailored']} appl={r['applied']} "
                      f"alert={r['alerted']} fail={r['failed']} skip={r['skipped']}")

    elif args.cmd == "pending":
        # Consumido pelo watcher do Hermes, que reporta as novidades no chat.
        import json as _json
        print(_json.dumps([
            {"uid": r["uid"], "title": r["title"], "company": r["company"],
             "location": r["location"], "score": r["score"], "url": r["url"],
             "changes_summary": r["changes_summary"], "pdf_path": r["pdf_path"]}
            for r in orch.tracker.pending_review()
        ], ensure_ascii=False))

    elif args.cmd == "tailor":
        summary, path = orch.tailor_url(args.url)
        print(summary)
        print(f"\nArquivo: {path}")

    elif args.cmd == "bot":
        from .telegram_bot import run_bot
        run_bot(orch)

    elif args.cmd == "run":
        from apscheduler.schedulers.background import BackgroundScheduler

        from .telegram_bot import run_bot

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            orch.run_cycle_locked, "interval",
            minutes=cfg.search.interval_minutes,
            next_run_time=__import__("datetime").datetime.now(),
            max_instances=1, coalesce=True,
        )
        scheduler.start()
        log.info("Scheduler ativo (a cada %d min). Iniciando bot...",
                 cfg.search.interval_minutes)
        if cfg.telegram.enabled and cfg.telegram.token:
            run_bot(orch)  # bloqueia; scheduler roda em background
        else:
            log.warning("Telegram desabilitado — rodando só o scheduler (Ctrl+C para sair)")
            threading.Event().wait()


if __name__ == "__main__":
    main()
