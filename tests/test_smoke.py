"""Smoke tests sem rede/LLM: config, models, db, rendering, sanity-check do tailoring."""
import json
from pathlib import Path

import pytest

from autoapply.config import Config, load_config
from autoapply.db import Tracker
from autoapply.idioma import detectar as detectar_idioma
from autoapply.llm import LLM, sampling_via_prompt
from autoapply.models import ApplicationStatus, Job, normalize
from autoapply.rendering import render_resume
from autoapply.tailoring import _sanity_check

RESUME = {
    "basics": {"name": "Rhayron", "email": "rhayron1717@gmail.com", "label": "Dev",
               "summary": "Resumo", "profiles": []},
    "work": [{"name": "ACME", "position": "Dev", "startDate": "2023-01",
              "endDate": "", "highlights": ["Fez X"]}],
    "skills": [{"name": "Backend", "keywords": ["Python"]}],
    "education": [], "languages": [], "projects": [],
}


def test_job_uid_stable():
    a = Job(source="greenhouse", external_id="1", title="Dev", company="acme", url="http://x")
    b = Job(source="greenhouse", external_id="1", title="Outro", company="acme", url="http://y")
    assert a.uid == b.uid


def test_tracker_roundtrip(tmp_path):
    t = Tracker(tmp_path / "t.db")
    j = Job(source="lever", external_id="abc", title="Dev", company="acme", url="http://x")
    assert not t.seen(j)
    t.add_job(j)
    assert t.seen(j)
    t.set_status(j.uid, ApplicationStatus.SCORED, score=88)
    assert t.get(j.uid)["score"] == 88
    assert t.applications_today() == 0


def test_normalize_ignora_acento_e_pontuacao():
    assert normalize("Software Engineer, Platform - São Paulo") == \
        "software engineer platform sao paulo"
    assert normalize("Backend  Engineer") == normalize("Backend - Engineer")


def test_normalize_preserva_niveis():
    # 'Engineer I' e 'Engineer II' são vagas diferentes: não podem colidir.
    assert normalize("Software Engineer I") != normalize("Software Engineer II")


def test_seen_pega_repost_com_outro_id(tmp_path):
    """O caso real que motivou isto: LinkedIn republicando a mesma vaga."""
    t = Tracker(tmp_path / "t.db")
    original = Job(source="linkedin", external_id="4437131504",
                   title="Software Engineer, Platform - São Paulo, Braz",
                   company="Speechify", url="http://x")
    repost = Job(source="linkedin", external_id="4442712638",
                 title="Software Engineer, Platform - Sao Paulo, Braz",
                 company="speechify", url="http://y")

    assert original.uid != repost.uid       # ids diferentes: o uid não salva
    t.add_job(original)
    assert t.seen(repost)                   # mas o conteúdo sim


def test_seen_pega_mesma_vaga_em_fontes_diferentes(tmp_path):
    t = Tracker(tmp_path / "t.db")
    t.add_job(Job(source="linkedin", external_id="1", title="Dev Backend",
                  company="ACME", url="http://x"))
    assert t.seen(Job(source="gupy", external_id="9", title="Dev  Backend",
                      company="acme", url="http://y"))


def test_seen_nao_confunde_vagas_distintas(tmp_path):
    t = Tracker(tmp_path / "t.db")
    t.add_job(Job(source="lever", external_id="1", title="Dev Backend",
                  company="ACME", url="http://x"))
    assert not t.seen(Job(source="lever", external_id="2", title="Dev Frontend",
                          company="ACME", url="http://y"))
    assert not t.seen(Job(source="lever", external_id="3", title="Dev Backend",
                          company="Outra Empresa", url="http://z"))


def test_migracao_preenche_dedupe_key_em_banco_antigo(tmp_path):
    """Banco de produção existe entre deploys: a coluna nova tem que ser
    adicionada e preenchida, não só declarada no SCHEMA."""
    import sqlite3

    db = tmp_path / "antigo.db"
    legado = sqlite3.connect(db)
    legado.execute("""CREATE TABLE jobs (
        uid TEXT PRIMARY KEY, source TEXT, external_id TEXT, title TEXT,
        company TEXT, location TEXT, url TEXT, description TEXT, posted_at TEXT,
        discovered_at TEXT, status TEXT, score INTEGER, score_reasoning TEXT,
        resume_json TEXT, cover_letter TEXT, changes_summary TEXT, pdf_path TEXT,
        fail_reason TEXT, applied_at TEXT, updated_at TEXT)""")
    legado.execute("INSERT INTO jobs (uid, source, external_id, title, company) "
                   "VALUES ('abc','linkedin','1','Dev Pleno','São Paulo Tech')")
    legado.commit()
    legado.close()

    t = Tracker(db)  # abrir já migra
    assert t.get("abc")["dedupe_key"] == "dev pleno|sao paulo tech"
    # e a linha migrada passa a bloquear o repost
    assert t.seen(Job(source="gupy", external_id="99", title="Dev  Pleno",
                      company="Sao Paulo Tech", url="http://x"))


def test_sampling_via_prompt_detecta_gemini_3_mais():
    assert sampling_via_prompt("gemini/gemini-3.6-flash")
    assert sampling_via_prompt("gemini/gemini-3.5-flash-lite")
    assert sampling_via_prompt("gemini-4-pro")            # 4 também é "3+"
    assert not sampling_via_prompt("gemini/gemini-2.0-flash")
    assert not sampling_via_prompt("anthropic/claude-sonnet-4-5")
    assert not sampling_via_prompt("openai/gpt-4o")


def test_temperatura_vira_orientacao_textual():
    frio = LLM("gemini/gemini-3.6-flash", temperature=0.3)
    quente = LLM("gemini/gemini-3.6-flash", temperature=1.2)
    assert "consistência" in frio._orientacao()
    assert frio._orientacao() != quente._orientacao()
    # Modelo antigo continua mandando o parâmetro, não o texto.
    assert not LLM("gemini/gemini-2.0-flash", temperature=0.3)._sampling_no_prompt


def test_deteccao_de_idioma():
    en = ("Senior Backend Engineer",
          "We are looking for a candidate with strong experience in Python and "
          "distributed systems. You will join our team and work on the API that "
          "powers our product. Requirements: 5 years of experience with backend "
          "development, and a solid understanding of databases.")
    pt = ("Pessoa Desenvolvedora Backend Sênior",
          "Buscamos uma pessoa com experiência sólida em Python e sistemas "
          "distribuídos. Você vai atuar na nossa equipe e trabalhar na API do "
          "produto. Requisitos: 5 anos de experiência com desenvolvimento backend "
          "e bom conhecimento de bancos de dados. É desejável conhecer Docker.")
    assert detectar_idioma(*en) == "en"
    assert detectar_idioma(*pt) == "pt"


def test_idioma_ignora_jargao_tecnico():
    """Stack é igual nos dois idiomas: não pode decidir sozinha o resultado."""
    pt = ("Desenvolvedor Python",
          "Vaga para atuar com Python, Docker, Kubernetes, FastAPI, PostgreSQL, "
          "React, AWS Lambda e Terraform. Requisitos: experiência com "
          "desenvolvimento de APIs, conhecimento de bancos de dados e que você "
          "saiba trabalhar em equipe na nossa empresa.")
    assert detectar_idioma(*pt) == "pt"


def test_idioma_texto_curto_fica_em_portugues():
    # Sem sinal suficiente, o conservador é não traduzir: o currículo base é PT.
    assert detectar_idioma("Backend Engineer", "") == "pt"


def _fonte(**busca):
    """Fonte mínima só para exercitar _passes_filters."""
    from autoapply.config import Config, SearchConfig
    from autoapply.discovery.base import JobSource

    class Fake(JobSource):
        name = "fake"

        def fetch(self):
            return []

    cfg = Config(search=SearchConfig(**busca))
    return Fake(cfg)


def test_filtro_de_local_e_aplicado():
    """locations existia no config e não filtrava nada: só alimentava o LinkedIn."""
    f = _fonte(titles=["Software Engineer"], locations=["Curitiba"])
    curitiba = Job(source="x", external_id="1", title="Software Engineer",
                   company="ACME", location="Curitiba, PR", url="http://x")
    recife = Job(source="x", external_id="2", title="Software Engineer",
                 company="ACME", location="Recife, PE", url="http://y")
    assert f._passes_filters(curitiba)
    assert not f._passes_filters(recife)


def test_local_no_corpo_da_vaga_conta():
    f = _fonte(titles=["Software Engineer"], locations=["Curitiba"])
    remota = Job(source="x", external_id="3", title="Software Engineer", company="ACME",
                 location="", url="http://z",
                 description="Remote role, team based in Curitiba.")
    assert f._passes_filters(remota)


def test_sem_filtro_de_local_passa_qualquer_lugar():
    f = _fonte(titles=["Software Engineer"])
    j = Job(source="x", external_id="4", title="Software Engineer", company="ACME",
            location="Recife, PE", url="http://w")
    assert f._passes_filters(j)


def test_keyword_de_nicho_bate_na_descricao():
    """Título genérico + termo de nicho no corpo: o caso de 'firmware embarcado'."""
    f = _fonte(titles=["Firmware Engineer"], keywords=["firmware embarcado"])
    j = Job(source="x", external_id="5", title="Software Engineer", company="ACME",
            url="http://x", description="Vaga para atuar com firmware embarcado em ARM.")
    assert f._passes_filters(j)

    fora = Job(source="x", external_id="6", title="Software Engineer", company="ACME",
               url="http://y", description="Vaga de marketing digital.")
    assert not f._passes_filters(fora)


def test_slug_nao_cria_subdiretorio():
    """Título real tem barra: 'AI/ML Backend Software Engineer | Senior (Remote)'."""
    from autoapply.rendering import sanitizar_slug

    s = sanitizar_slug("Compass UOL-AI/ML Backend Software Engineer | Senior (Remote)-23bcdd")
    assert "/" not in s and "\\" not in s and "|" not in s
    assert not s.startswith("-") and not s.endswith("-")
    assert len(s) <= 80
    assert sanitizar_slug("///") == "curriculo"


def test_aplicar_mudancas_respeita_whitelist(tmp_path):
    import yaml as _yaml

    from autoapply.config import aplicar_mudancas

    p = tmp_path / "config.yaml"
    p.write_text(_yaml.safe_dump({
        "mode": "review",
        "search": {"locations": ["Brazil"], "titles": ["Dev"]},
        "output": {"dir": "out", "db_path": "autoapply.db"},
    }), encoding="utf-8")

    r = aplicar_mudancas(p, {"search.locations": ["Curitiba"],
                             "output.db_path": "/tmp/invadido.db"})
    assert r["aplicados"] == {"search.locations": ["Curitiba"]}
    assert "output.db_path" in r["recusados"]

    d = _yaml.safe_load(p.read_text(encoding="utf-8"))
    assert d["search"]["locations"] == ["Curitiba"]
    assert d["output"]["db_path"] == "autoapply.db"   # intacto


def test_aplicar_mudancas_nao_grava_config_invalida(tmp_path):
    import yaml as _yaml

    from autoapply.config import aplicar_mudancas

    p = tmp_path / "config.yaml"
    original = {"mode": "review", "search": {"max_age_days": 7}}
    p.write_text(_yaml.safe_dump(original), encoding="utf-8")

    r = aplicar_mudancas(p, {"search.max_age_days": "isso não é número"})
    assert "erro" in r and not r["aplicados"]
    assert _yaml.safe_load(p.read_text(encoding="utf-8")) == original


def test_render_resume(tmp_path):
    html, pdf = render_resume(RESUME, tmp_path, "teste")
    content = html.read_text(encoding="utf-8")
    assert "Rhayron" in content and "ACME" in content


def test_sanity_check_blocks_invented_experience():
    tailored = json.loads(json.dumps(RESUME))
    tailored["work"].append({"name": "Google", "position": "CTO", "startDate": "2020-01"})
    with pytest.raises(ValueError):
        _sanity_check(RESUME, tailored)


def test_sanity_check_restores_basics():
    tailored = json.loads(json.dumps(RESUME))
    tailored["basics"]["email"] = "fake@x.com"
    _sanity_check(RESUME, tailored)
    assert tailored["basics"]["email"] == "rhayron1717@gmail.com"


def test_config_defaults(tmp_path):
    cfg = load_config(tmp_path / "nao-existe.yaml")
    assert cfg.mode.value == "review"
    assert cfg.matching.apply_threshold == 75
