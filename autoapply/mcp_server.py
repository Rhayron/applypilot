"""Servidor MCP: expõe o autopilot como ferramentas para o Hermes.

O Hermes é o maestro — ele decide quando rodar um ciclo, lê os resultados, ajusta a
configuração e conversa com o usuário. Este módulo é a superfície que ele controla.

Transporte: stdio, no mesmo padrão do mechabrain. O Hermes conecta com

    docker exec -i autocv-autoapply-1 autoapply -c /data/config.yaml mcp

Cada conexão abre um processo novo dentro do container. O estado que ele compartilha
com o scheduler que roda ali ao lado é o SQLite (em WAL, que aguenta multiprocesso) e
o próprio config.yaml — por isso `run_cycle` pega um lock de arquivo antes de rodar,
para que um ciclo pedido pelo Hermes nunca se sobreponha ao ciclo do scheduler.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

# Campos que o Hermes pode ajustar sozinho. Tudo que não estiver aqui é recusado —
# o agente não deve conseguir reescrever caminhos de perfil, banco ou credenciais.
SETTABLE = (
    "mode",
    "matching.apply_threshold",
    "matching.alert_threshold",
    "search.interval_minutes",
    "search.titles",
    "search.keywords",
    "search.locations",
    "search.remote_only",
    "search.max_age_days",
    "limits.max_applications_per_day",
    "limits.min_seconds_between_applications",
    "llm.temperature",
)

# Colunas pesadas: nunca voltam numa listagem, só sob demanda em job_detail.
_HEAVY = ("description", "resume_json")


def _row(row, *, heavy: bool = False) -> dict:
    d = dict(row)
    if not heavy:
        for k in _HEAVY:
            d.pop(k, None)
    return d


def _brief(row) -> dict:
    """Versão compacta para listagens — o suficiente para o Hermes decidir e narrar."""
    return {
        "uid": row["uid"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "score": row["score"],
        "status": row["status"],
        "url": row["url"],
    }


def build_server(config_path: str):
    from mcp.server.fastmcp import FastMCP

    from .config import load_config
    from .orchestrator import Orchestrator

    cfg = load_config(config_path)
    orch = Orchestrator(cfg)
    mcp = FastMCP("autopilot")

    # ---------------------------------------------------------------- leitura
    @mcp.tool()
    def status() -> dict:
        """Panorama do autopilot: quantas vagas em cada status, aplicações de hoje
        contra o limite diário, e o modo de operação atual."""
        return {
            "mode": orch.cfg.mode.value,
            "por_status": orch.tracker.stats(),
            "aplicacoes_hoje": orch.tracker.applications_today(),
            "limite_diario": orch.cfg.limits.max_applications_per_day,
            "intervalo_minutos": orch.cfg.search.interval_minutes,
        }

    @mcp.tool()
    def metrics(limit: int = 10) -> dict:
        """Histórico de ciclos: totais acumulados e os últimos `limit` ciclos."""
        return {
            "totais": orch.tracker.cycle_totals(),
            "ultimos_ciclos": [dict(r) for r in orch.tracker.cycle_history(limit)],
        }

    @mcp.tool()
    def list_jobs(status: Optional[str] = None, min_score: Optional[int] = None,
                  limit: int = 20) -> list[dict]:
        """Lista vagas, opcionalmente filtrando por status e nota mínima.

        Status possíveis: discovered, scored, skipped, tailored, pending_review,
        approved, applied, failed, alerted, rejected_by_user.
        """
        sql = "SELECT * FROM jobs WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if min_score is not None:
            sql += " AND score >= ?"
            params.append(min_score)
        sql += " ORDER BY COALESCE(score, -1) DESC, discovered_at DESC LIMIT ?"
        params.append(limit)
        with orch.tracker._lock:
            rows = orch.tracker.conn.execute(sql, params).fetchall()
        return [_brief(r) for r in rows]

    @mcp.tool()
    def pending_review() -> list[dict]:
        """Vagas com CV pronto esperando a decisão do usuário. É a fila que o Hermes
        deve reportar no chat — cada uma traz o resumo do que foi adaptado e o
        caminho do PDF dentro do container."""
        out = []
        for r in orch.tracker.pending_review():
            d = _brief(r)
            d["score_reasoning"] = r["score_reasoning"]
            d["changes_summary"] = r["changes_summary"]
            d["pdf_path"] = r["pdf_path"]
            out.append(d)
        return out

    @mcp.tool()
    def job_detail(uid: str) -> dict:
        """Detalhe completo de uma vaga: nota e justificativa, resumo das adaptações,
        cover letter e caminho do PDF. A descrição da vaga vem truncada em 4000
        caracteres."""
        row = orch.tracker.get(uid)
        if not row:
            return {"erro": f"vaga {uid} não encontrada"}
        d = _row(row, heavy=True)
        if d.get("description"):
            d["description"] = d["description"][:4000]
        d.pop("resume_json", None)  # JSON Resume inteiro não ajuda o agente
        return d

    @mcp.tool()
    def get_config() -> dict:
        """Configuração atual e a lista de campos que podem ser alterados via
        set_config."""
        return {
            "mode": orch.cfg.mode.value,
            "matching": orch.cfg.matching.model_dump(),
            "search": orch.cfg.search.model_dump(),
            "limits": orch.cfg.limits.model_dump(),
            "llm": {"model": orch.cfg.llm.model, "cheap_model": orch.cfg.llm.cheap_model,
                    "temperature": orch.cfg.llm.temperature},
            "ajustaveis": list(SETTABLE),
        }

    # ------------------------------------------------------------------ ação
    @mcp.tool()
    def run_cycle() -> dict:
        """Roda um ciclo agora: descobre vagas, pontua, adapta o CV das aprovadas e
        põe na fila de revisão. Não candidata a nada — em mode=review a decisão final
        é sempre do usuário. Retorna as estatísticas do ciclo.

        Se o scheduler já estiver no meio de um ciclo, devolve ocupado em vez de
        rodar em paralelo."""
        stats = orch.run_cycle_locked()
        if stats is None:
            return {"ocupado": True,
                    "detalhe": "um ciclo já está em andamento; tente de novo em instantes"}
        return stats

    @mcp.tool()
    def tailor_url(url: str) -> dict:
        """Adapta o currículo para uma vaga específica a partir da URL dela, fora do
        fluxo de descoberta. Útil quando o usuário manda um link no chat."""
        summary, path = orch.tailor_url(url)
        return {"resumo": summary, "arquivo": str(path) if path else None}

    @mcp.tool()
    def set_config(changes: dict) -> dict:
        """Ajusta a configuração do autopilot. Recebe um dict de caminho pontilhado
        para valor, por exemplo {"matching.alert_threshold": 70,
        "search.interval_minutes": 120}.

        Só aceita os campos listados em get_config().ajustaveis. Grava no config.yaml
        e vale a partir do próximo ciclo — o scheduler relê o arquivo a cada rodada,
        então não precisa reiniciar nada. Para aplicar na hora, chame run_cycle em
        seguida."""
        path = Path(config_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        aplicados, recusados = {}, {}
        for dotted, value in changes.items():
            if dotted not in SETTABLE:
                recusados[dotted] = "campo não ajustável"
                continue
            node = data
            parts = dotted.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = value
            aplicados[dotted] = value

        if aplicados:
            # Valida antes de gravar: config quebrada derruba o scheduler no próximo ciclo.
            from .config import Config
            try:
                Config(**data)
            except Exception as e:  # noqa: BLE001
                return {"erro": f"configuração inválida, nada foi gravado: {e}"}
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                            encoding="utf-8")
            orch.reload_config()

        return {"aplicados": aplicados, "recusados": recusados,
                "vale_a_partir_de": "próximo ciclo"}

    @mcp.tool()
    def reject_job(uid: str) -> dict:
        """Marca uma vaga como descartada pelo usuário. Ela sai da fila de revisão e
        não volta a aparecer."""
        from .models import ApplicationStatus
        if not orch.tracker.get(uid):
            return {"erro": f"vaga {uid} não encontrada"}
        orch.tracker.set_status(uid, ApplicationStatus.REJECTED_BY_USER)
        return {"uid": uid, "status": "rejected_by_user"}

    @mcp.tool()
    def apply_job(uid: str) -> dict:
        """AÇÃO IRREVERSÍVEL: envia de verdade a candidatura do usuário para esta
        vaga, com o nome e o currículo dele.

        NUNCA chame sem o usuário ter aprovado explicitamente esta vaga específica no
        chat. Aprovação para uma vaga não vale para outra. Só funciona em vagas que
        estão em pending_review."""
        row = orch.tracker.get(uid)
        if not row:
            return {"erro": f"vaga {uid} não encontrada"}
        if row["status"] != "pending_review":
            return {"erro": f"vaga está em '{row['status']}', não em 'pending_review'; "
                            "só candidaturas revisadas podem ser enviadas"}
        ok = orch.apply_by_uid(uid)
        return {"uid": uid, "enviada": ok,
                "status": dict(orch.tracker.get(uid)).get("status")}

    return mcp


def run(config_path: str) -> None:
    # stdio é o canal do protocolo: nada além do MCP pode escrever em stdout.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)
    build_server(config_path).run()
