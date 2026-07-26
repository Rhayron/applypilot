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


def _anotacoes(titulo: str, *, leitura: bool = False, destrutiva: bool = False):
    """Declara a natureza da ferramenta para o cliente MCP.

    É por aqui que o Hermes distingue uma consulta de uma ação com consequência.
    Sem isso todas as ferramentas parecem iguais para ele, e não há sinal de que
    `apply_job` manda uma candidatura de verdade enquanto `status` só lê o banco.
    """
    try:
        from mcp.types import ToolAnnotations
    except ImportError:  # servidor antigo: seguir sem anotação é melhor que quebrar
        return None
    return ToolAnnotations(
        title=titulo,
        readOnlyHint=leitura,
        destructiveHint=destrutiva,
        idempotentHint=leitura,
        openWorldHint=not leitura,
    )


def build_server(config_path: str):
    from mcp.server.fastmcp import FastMCP

    from .config import load_config
    from .orchestrator import Orchestrator

    cfg = load_config(config_path)
    orch = Orchestrator(cfg)
    mcp = FastMCP("autopilot")

    LEITURA = dict(leitura=True)
    ESCRITA = dict(leitura=False)
    IRREVERSIVEL = dict(leitura=False, destrutiva=True)

    # ---------------------------------------------------------------- leitura
    @mcp.tool(annotations=_anotacoes("Panorama do autopilot", **LEITURA))
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

    @mcp.tool(annotations=_anotacoes("Histórico de ciclos", **LEITURA))
    def metrics(limit: int = 10) -> dict:
        """Histórico de ciclos: totais acumulados e os últimos `limit` ciclos."""
        return {
            "totais": orch.tracker.cycle_totals(),
            "ultimos_ciclos": [dict(r) for r in orch.tracker.cycle_history(limit)],
        }

    @mcp.tool(annotations=_anotacoes("Listar vagas", **LEITURA))
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

    @mcp.tool(annotations=_anotacoes("Fila de decisões", **LEITURA))
    def awaiting_decision() -> list[dict]:
        """Vagas esperando decisão do usuário. É a fila para reportar no chat.

        `espera` diz o que falta em cada uma:
        - "gerar_cv": passou no corte, currículo ainda não existe. Pergunte se ele
          quer gerar e com qual editor (claude ou gemini), depois chame gerar_cv.
        - "aplicar": currículo pronto e a fonte aceita envio automático. Peça
          aprovação e então chame apply_job.
        - "envio_manual": currículo pronto, mas a fonte não tem automação. Entregue
          o PDF e o link para ele se candidatar à mão. É a maioria dos casos.
        """
        espera_por = {"pending_generation": "gerar_cv",
                      "pending_review": "aplicar",
                      "alerted": "envio_manual"}
        out = []
        for r in orch.tracker.awaiting_decision():
            d = _brief(r)
            d["espera"] = espera_por.get(r["status"], r["status"])
            d["score_reasoning"] = r["score_reasoning"]
            d["changes_summary"] = r["changes_summary"]
            d["pdf_path"] = r["pdf_path"]
            d["auto_aplicavel"] = r["status"] == "pending_review"
            out.append(d)
        return out

    @mcp.tool(annotations=_anotacoes("Detalhe da vaga", **LEITURA))
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

    @mcp.tool(annotations=_anotacoes("Ler configuração", **LEITURA))
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
    @mcp.tool(annotations=_anotacoes("Rodar um ciclo de busca", **ESCRITA))
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

    @mcp.tool(annotations=_anotacoes("Gerar currículo adaptado (gasta LLM)", **ESCRITA))
    def gerar_cv(uid: str, editor: str = "auto") -> dict:
        """Gera o currículo adaptado para uma vaga e manda o PDF no chat.

        Chame quando o usuário decidir que quer o currículo daquela vaga. `editor`:
        "claude" (melhor texto, mais lento), "gemini" (mais rápido e conservador,
        preserva mais do original) ou "auto" (Claude com Gemini de reserva). Se o
        usuário não disser qual, pergunte antes de chamar.

        Custa uma chamada cara de LLM e produz um documento no nome do usuário, então
        não chame por iniciativa própria em vagas que ele não pediu."""
        if editor not in ("auto", "claude", "gemini"):
            return {"erro": f"editor inválido: {editor!r}. Use claude, gemini ou auto"}
        row = orch.tracker.get(uid)
        if not row:
            return {"erro": f"vaga {uid} não encontrada"}

        arquivo = orch.gerar_cv(uid, editor=editor)
        if not arquivo:
            return {"erro": "falha ao gerar o currículo; veja os logs"}
        atual = orch.tracker.get(uid)
        return {
            "uid": uid,
            "arquivo": str(arquivo),
            "status": atual["status"],
            "auto_aplicavel": atual["status"] == "pending_review",
            "mudancas": atual["changes_summary"],
            "enviado_no_chat": True,
        }

    @mcp.tool(annotations=_anotacoes("Adaptar CV para uma URL", **ESCRITA))
    def tailor_url(url: str) -> dict:
        """Adapta o currículo para uma vaga específica a partir da URL dela, fora do
        fluxo de descoberta. Útil quando o usuário manda um link no chat."""
        summary, path = orch.tailor_url(url)
        return {"resumo": summary, "arquivo": str(path) if path else None}

    @mcp.tool(annotations=_anotacoes("Alterar configuração", **ESCRITA))
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

    @mcp.tool(annotations=_anotacoes("Ajustar filtros de busca", **ESCRITA))
    def ajustar_busca(
        adicionar_titulos: Optional[list[str]] = None,
        remover_titulos: Optional[list[str]] = None,
        adicionar_palavras: Optional[list[str]] = None,
        remover_palavras: Optional[list[str]] = None,
        locais: Optional[list[str]] = None,
        somente_remoto: Optional[bool] = None,
        dias_maximos: Optional[int] = None,
    ) -> dict:
        """Ajusta os filtros de busca de vaga, somando ou tirando itens.

        É a ferramenta para pedidos em linguagem natural do usuário. Exemplos:
        - "procure vagas só em Curitiba"     -> locais=["Curitiba"]
        - "quero vagas de firmware embarcado" ->
              adicionar_titulos=["Firmware", "Embedded", "Engenheiro de Software
              Embarcado"], adicionar_palavras=["firmware", "embarcado", "rtos",
              "microcontrolador", "c/c++"]
        - "volta a aceitar de qualquer lugar" -> locais=[]

        Como funciona a filtragem, para você escolher bem os termos: uma vaga entra
        se o TÍTULO contém algum de `titulos`, ou se o título OU a descrição contêm
        alguma de `palavras`. Então ponha em `titulos` o que costuma aparecer no nome
        do cargo, e em `palavras` os termos de nicho que aparecem no corpo do anúncio.
        `locais` casa contra local, título e descrição; lista vazia remove o filtro.

        Ao atender um pedido amplo ("e afins"), inclua você mesmo as variações
        equivalentes, inclusive em inglês, já que boa parte das fontes publica assim.
        Os campos não informados ficam como estão.
        """
        atual = orch.cfg.search
        titulos = list(atual.titles)
        palavras = list(atual.keywords)

        def _fora(lista: list[str], remover: list[str]) -> list[str]:
            alvo = {r.strip().lower() for r in remover}
            return [x for x in lista if x.strip().lower() not in alvo]

        def _dentro(lista: list[str], somar: list[str]) -> list[str]:
            existentes = {x.strip().lower() for x in lista}
            return lista + [s.strip() for s in somar
                            if s.strip() and s.strip().lower() not in existentes]

        if remover_titulos:
            titulos = _fora(titulos, remover_titulos)
        if adicionar_titulos:
            titulos = _dentro(titulos, adicionar_titulos)
        if remover_palavras:
            palavras = _fora(palavras, remover_palavras)
        if adicionar_palavras:
            palavras = _dentro(palavras, adicionar_palavras)

        mudancas: dict[str, Any] = {}
        if titulos != atual.titles:
            mudancas["search.titles"] = titulos
        if palavras != atual.keywords:
            mudancas["search.keywords"] = palavras
        if locais is not None:
            mudancas["search.locations"] = [x.strip() for x in locais if x.strip()]
        if somente_remoto is not None:
            mudancas["search.remote_only"] = somente_remoto
        if dias_maximos is not None:
            mudancas["search.max_age_days"] = dias_maximos

        if not mudancas:
            return {"aviso": "nada mudou", "busca_atual": orch.cfg.search.model_dump()}

        resultado = set_config(mudancas)
        resultado["busca_atual"] = orch.cfg.search.model_dump()
        resultado["observacao"] = (
            "Vale no próximo ciclo. Para valer agora, chame run_cycle. "
            "Filtro novo não reavalia vaga já vista."
        )
        return resultado

    @mcp.tool(annotations=_anotacoes("Descartar vaga", **ESCRITA))
    def reject_job(uid: str) -> dict:
        """Marca uma vaga como descartada pelo usuário. Ela sai da fila de revisão e
        não volta a aparecer."""
        from .models import ApplicationStatus
        if not orch.tracker.get(uid):
            return {"erro": f"vaga {uid} não encontrada"}
        orch.tracker.set_status(uid, ApplicationStatus.REJECTED_BY_USER)
        return {"uid": uid, "status": "rejected_by_user"}

    @mcp.tool(annotations=_anotacoes("ENVIAR CANDIDATURA (irreversível)", **IRREVERSIVEL))
    def apply_job(uid: str) -> dict:
        """AÇÃO IRREVERSÍVEL: envia de verdade a candidatura do usuário para esta
        vaga, com o nome e o currículo dele.

        NUNCA chame sem o usuário ter aprovado explicitamente esta vaga específica no
        chat. Aprovação para uma vaga não vale para outra. Só funciona em vagas com
        auto_aplicavel=True; para as demais, oriente o usuário a aplicar à mão."""
        row = orch.tracker.get(uid)
        if not row:
            return {"erro": f"vaga {uid} não encontrada"}
        if row["status"] == "alerted":
            return {"erro": "esta fonte não tem automação de envio (auto_aplicavel=False). "
                            "Entregue o PDF e o link para o usuário se candidatar à mão.",
                    "url": row["url"], "pdf_path": row["pdf_path"]}
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
