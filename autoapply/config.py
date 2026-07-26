"""Carrega config.yaml + .env e expõe o perfil do usuário."""
from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .models import Mode


class LLMConfig(BaseModel):
    model: str = "anthropic/claude-sonnet-4-5"
    cheap_model: Optional[str] = None
    temperature: float = 0.3

    @property
    def scoring_model(self) -> str:
        return self.cheap_model or self.model


class ProfileConfig(BaseModel):
    resume_path: str = "profile/resume.json"
    context_path: str = "profile/context.md"
    answers_path: str = "profile/answers.yaml"
    # Currículo real do usuário. Quando existe, a adaptação passa a ser uma edição
    # pontual deste arquivo em vez da montagem de um documento novo.
    docx_path: str = "profile/base.docx"


class Modalidade(str, Enum):
    REMOTO = "remoto"
    PRESENCIAL = "presencial"
    AMBOS = "ambos"        # híbrido: aceita as duas


class SearchConfig(BaseModel):
    interval_minutes: int = 180
    titles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_only: bool = False
    modalidade: Optional[Modalidade] = None
    max_age_days: int = 7

    @property
    def modo(self) -> Modalidade:
        """Modalidade efetiva.

        `remote_only` é booleano e não comporta "aceito os dois", que é justamente o
        caso híbrido. `modalidade` substitui o flag; quando ausente, é derivada dele
        para não quebrar config antiga nem exigir migração do arquivo.
        """
        if self.modalidade is not None:
            return self.modalidade
        return Modalidade.REMOTO if self.remote_only else Modalidade.AMBOS


class MatchingConfig(BaseModel):
    apply_threshold: int = 75
    alert_threshold: int = 60


class LimitsConfig(BaseModel):
    max_applications_per_day: int = 20
    min_seconds_between_applications: int = 300


class TelegramConfig(BaseModel):
    """Envio e polling são coisas separadas.

    Desde que o Hermes virou o maestro, o autopilot manda as mensagens pelo bot
    *dele* — mesma conversa, mesma identidade. Mas quem faz getUpdates naquele token
    é o gateway do Hermes: se o autopilot também fizesse polling, os dois brigariam
    e a Bot API derrubaria um com `Conflict: terminated by other getUpdates request`.
    Por isso `bot` é falso por padrão: enviar pode, escutar não.
    """

    enabled: bool = True   # manda alertas (sendMessage/sendDocument)
    bot: bool = False      # roda o bot próprio com polling e botões

    @property
    def token(self) -> str:
        return os.environ.get("TELEGRAM_BOT_TOKEN", "")

    @property
    def chat_id(self) -> str:
        return os.environ.get("TELEGRAM_CHAT_ID", "")


class OutputConfig(BaseModel):
    dir: str = "out"
    db_path: str = "autoapply.db"


class Config(BaseModel):
    mode: Mode = Mode.REVIEW
    llm: LLMConfig = Field(default_factory=LLMConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    base_dir: Path = Path(".")

    # ---- perfil ----
    def load_resume(self) -> dict:
        p = self.base_dir / self.profile.resume_path
        if not p.exists():
            raise FileNotFoundError(
                f"Currículo base não encontrado em {p}. "
                "Copie profile/resume.example.json para profile/resume.json e preencha."
            )
        return json.loads(p.read_text(encoding="utf-8"))

    def load_context(self) -> str:
        p = self.base_dir / self.profile.context_path
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def load_answers(self) -> dict:
        p = self.base_dir / self.profile.answers_path
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    def source(self, name: str) -> dict[str, Any]:
        return self.sources.get(name, {})

    @property
    def base_docx(self) -> Optional[Path]:
        p = self.base_dir / self.profile.docx_path
        return p if p.exists() else None

    @property
    def out_dir(self) -> Path:
        d = self.base_dir / self.output.dir
        d.mkdir(parents=True, exist_ok=True)
        return d


# Campos ajustáveis em runtime, pelo bot ou pelo MCP. O que não está aqui só muda
# editando o arquivo à mão: caminhos de perfil, banco e credenciais ficam fora do
# alcance de quem conversa com o agente.
SETTABLE = (
    "mode",
    "matching.apply_threshold",
    "matching.alert_threshold",
    "search.interval_minutes",
    "search.titles",
    "search.keywords",
    "search.locations",
    "search.remote_only",
    "search.modalidade",
    "search.max_age_days",
    "limits.max_applications_per_day",
    "limits.min_seconds_between_applications",
    "llm.temperature",
)


def aplicar_mudancas(path: str | Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Grava alterações no config.yaml, validando antes.

    Recebe caminhos pontilhados ("search.locations") e devolve o que entrou, o que
    foi recusado e o erro, se houver. Compartilhado pelo bot e pelo servidor MCP para
    que a whitelist e a validação não existam em duas versões que divergem.
    """
    path = Path(path)
    data: dict = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    aplicados, recusados = {}, {}
    for dotted, value in changes.items():
        if dotted not in SETTABLE:
            recusados[dotted] = "campo não ajustável"
            continue
        node = data
        partes = dotted.split(".")
        for p in partes[:-1]:
            node = node.setdefault(p, {})
        node[partes[-1]] = value
        aplicados[dotted] = value

    if aplicados:
        try:
            Config(**data)   # config quebrada derruba o scheduler no próximo ciclo
        except Exception as e:  # noqa: BLE001
            return {"aplicados": {}, "recusados": recusados,
                    "erro": f"configuração inválida, nada foi gravado: {e}"}
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")

    return {"aplicados": aplicados, "recusados": recusados}


def load_config(path: str | Path = "config.yaml") -> Config:
    load_dotenv()
    path = Path(path)
    data: dict = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config(**data)
    cfg.base_dir = path.parent if path.parent != Path("") else Path(".")
    return cfg
