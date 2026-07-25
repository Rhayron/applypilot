#!/usr/bin/env python3
"""Watcher do autopilot para o cron do Hermes (job no_agent).

Roda dentro do container do Hermes, pergunta ao autopilot quais vagas estão
esperando decisão e imprime as novidades em PT-BR. O Hermes pega o stdout e manda
no chat.

Segue o padrão watchdog dos outros jobs desta VPS: stdout vazio = nada a dizer,
nenhuma mensagem é enviada. Só avisa sobre vaga que ainda não foi anunciada, para
não repetir a mesma lista a cada rodada.

Instalação: /opt/data/scripts/autopilot_watch.py  (= ~/hermes-data/scripts/ no host)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

CONTAINER = "autocv-autoapply-1"
SEEN = Path("/opt/data/autopilot_seen.json")
MAX_DETALHE = 6  # acima disso vira resumo, para não estourar a mensagem


def pending() -> list[dict]:
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "autoapply", "-c", "/data/config.yaml", "pending"],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        # Falha real merece aviso: o autopilot pode estar fora do ar.
        print(f"⚠️ Autopilot não respondeu (exit {out.returncode}): "
              f"{(out.stderr or '').strip()[:300]}")
        raise SystemExit(0)
    return json.loads(out.stdout or "[]")


def main() -> None:
    vagas = pending()
    vistas = set(json.loads(SEEN.read_text()) if SEEN.exists() else [])
    novas = [v for v in vagas if v["uid"] not in vistas]

    if not novas:
        return  # silêncio

    linhas = [f"🎯 <b>{len(novas)} vaga(s) nova(s) aguardando sua decisão</b>"]
    for v in novas[:MAX_DETALHE]:
        linhas.append("")
        linhas.append(f"<b>{v['title']}</b> @ {v['company']}")
        linhas.append(f"📍 {v['location'] or 'n/d'} · Score {v['score']}/100")
        linhas.append(f"🔗 {v['url']}")
        if v.get("changes_summary"):
            linhas.append(f"✏️ {v['changes_summary'][:220]}")
        linhas.append("🤖 posso enviar a candidatura" if v.get("auto_aplicavel")
                      else "✍️ esta fonte exige envio manual")
        linhas.append(f"<code>{v['uid']}</code>")
    if len(novas) > MAX_DETALHE:
        linhas.append("")
        linhas.append(f"…e mais {len(novas) - MAX_DETALHE}. Peça a lista completa.")

    linhas.append("")
    linhas.append("💬 Me peça o CV de uma delas, ou diga para aplicar/descartar.")
    print("\n".join(linhas))

    SEEN.write_text(json.dumps(sorted(vistas | {v["uid"] for v in novas})))


if __name__ == "__main__":
    main()
