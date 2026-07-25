"""Smoke tests sem rede/LLM: config, models, db, rendering, sanity-check do tailoring."""
import json
from pathlib import Path

import pytest

from autoapply.config import Config, load_config
from autoapply.db import Tracker
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
