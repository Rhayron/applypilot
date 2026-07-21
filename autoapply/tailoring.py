"""Adaptação dinâmica do currículo para cada vaga (com trava de honestidade)."""
from __future__ import annotations

import json
import logging

from .llm import LLM
from .models import Job, TailoredApplication

log = logging.getLogger(__name__)

SYSTEM = """Você adapta currículos para vagas específicas.

REGRAS INEGOCIÁVEIS DE HONESTIDADE:
1. NUNCA invente experiências, empresas, cargos, datas, números, tecnologias ou certificações.
2. Você só pode: reescrever bullets com outras palavras, reordenar itens, omitir itens
   irrelevantes, ajustar o summary e destacar keywords da vaga QUE JÁ EXISTEM no perfil.
3. Toda skill mencionada deve existir no currículo base ou no contexto do candidato.
4. Se a vaga pede algo que o candidato não tem, NÃO adicione — deixe de fora.

Objetivo: máxima aderência ATS/recrutador mantendo 100% dos fatos.
Mantenha o schema JSON Resume idêntico ao de entrada (mesmas chaves).

Retorne JSON:
{
  "resume": { ...JSON Resume adaptado... },
  "cover_letter": "carta curta (<=200 palavras), no idioma da vaga, específica e sem clichês",
  "changes_summary": "bullets do que foi adaptado e por quê (no idioma do candidato)"
}"""


def tailor(llm: LLM, job: Job, resume: dict, context: str) -> TailoredApplication:
    user = f"""## Vaga alvo
Título: {job.title}
Empresa: {job.company}
Descrição:
{job.description[:8000]}

## Currículo base (fonte da verdade — não invente nada fora daqui)
{json.dumps(resume, ensure_ascii=False)}

## Contexto do candidato
{context[:20000]}
"""
    # o resume completo é reemitido na resposta, então o teto precisa acomodá-lo inteiro
    data = llm.complete_json(SYSTEM, user, max_tokens=16384)
    tailored = data.get("resume") or resume
    _sanity_check(resume, tailored)
    return TailoredApplication(
        job_uid=job.uid,
        resume_json=tailored,
        cover_letter=data.get("cover_letter", ""),
        changes_summary=data.get("changes_summary", ""),
    )


def _sanity_check(base: dict, tailored: dict) -> None:
    """Garante que dados imutáveis não foram alterados/inventados."""
    b, t = base.get("basics", {}), tailored.get("basics", {})
    for field in ("name", "email", "phone"):
        if b.get(field) and t.get(field) != b.get(field):
            log.warning("Tailoring alterou basics.%s — restaurando valor original", field)
            t[field] = b[field]
    # empresas/datas de experiência não podem mudar
    base_companies = {(w.get("name"), w.get("startDate")) for w in base.get("work", [])}
    for w in tailored.get("work", []):
        key = (w.get("name"), w.get("startDate"))
        if key not in base_companies:
            raise ValueError(
                f"Tailoring inventou/alterou experiência: {key}. Aplicação abortada."
            )
