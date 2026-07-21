"""Smoke tests sem rede/LLM: config, models, db, rendering, sanity-check do tailoring."""
import json
from pathlib import Path

import pytest

from autoapply.config import Config, load_config
from autoapply.db import Tracker
from autoapply.models import ApplicationStatus, Job
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
