# AutoApply — Especificação Técnica (v0.1)

**Autor do produto:** Rhayron (rhayron1717@gmail.com)
**Status:** Implementado (MVP) · **Data:** 2026-07-21

---

## 1. Visão geral

Agente autônomo que busca vagas de emprego periodicamente em múltiplas fontes, avalia o fit com o perfil do usuário via LLM, adapta o currículo dinamicamente para cada vaga (sem fabricar informações), aplica automaticamente quando possível e notifica o usuário pelo Telegram — sempre com o currículo adaptado em anexo quando não consegue aplicar.

### 1.1 Objetivos
- Reduzir o esforço de candidatura mantendo qualidade e personalização por vaga.
- Garantir honestidade absoluta: o CV adaptado nunca contém fatos inexistentes no perfil base.
- Manter o usuário no controle: modos de operação configuráveis e human-in-the-loop.

### 1.2 Não-objetivos
- Burlar CAPTCHAs, detecção de bots ou ToS de plataformas (LinkedIn nunca é automatizado).
- Gerenciar múltiplos usuários (single-user, local-first).
- Substituir o julgamento do usuário em perguntas sensíveis de screening.

---

## 2. Requisitos

### 2.1 Funcionais

| ID | Requisito | Status |
|----|-----------|--------|
| RF01 | Buscar vagas periodicamente (intervalo configurável) em Greenhouse, Lever, Ashby, Gupy, LinkedIn (público) e feeds RSS | ✅ |
| RF02 | Deduplicar vagas entre execuções via UID estável (`sha256(source:external_id)`) | ✅ |
| RF03 | Pontuar fit vaga×perfil (0–100) com justificativa, forças e gaps, via LLM | ✅ |
| RF04 | Adaptar o currículo (JSON Resume) por vaga: reescrever, reordenar, omitir — nunca inventar | ✅ |
| RF05 | Validar programaticamente que empresas, datas e contatos não foram alterados; abortar se violado | ✅ |
| RF06 | Gerar cover letter curta no idioma da vaga | ✅ |
| RF07 | Renderizar CV em HTML e PDF ATS-friendly (1 coluna, sem tabelas) | ✅ |
| RF08 | Aplicar automaticamente em Greenhouse e Lever, incluindo perguntas de screening respondidas via LLM a partir de banco de respostas | ✅ |
| RF09 | Falhar com segurança (CAPTCHA, pergunta sem resposta confiável, campo desconhecido) → alerta Telegram com vaga + motivo + CV adaptado | ✅ |
| RF10 | Modos configuráveis: `auto`, `review` (aprovação via botões no Telegram), `alert` | ✅ |
| RF11 | Tailoring on-demand: usuário envia URL no Telegram e recebe CV adaptado | ✅ |
| RF12 | Tracking completo em SQLite: status, score, CV usado, motivo de falha, timestamps | ✅ |
| RF13 | Limites de segurança: máx. aplicações/dia e intervalo mínimo entre aplicações | ✅ |
| RF14 | LLM multi-provider (Anthropic, OpenAI, Gemini, xAI/Grok, OpenRouter) trocável por config, com modelo barato separado para scoring | ✅ |
| RF15 | Comandos Telegram: `/start`, `/status`, `/pending` | ✅ |

### 2.2 Não-funcionais

| ID | Requisito |
|----|-----------|
| RNF01 | **Privacidade local-first**: dados do perfil ficam na máquina do usuário; só saem para o provedor LLM escolhido |
| RNF02 | **Resiliência**: falha em uma fonte/vaga não interrompe o ciclo; toda exceção é registrada no tracker |
| RNF03 | **Extensibilidade**: nova fonte = subclasse de `JobSource`; novo ATS = subclasse de `Applier` (registro em 1 linha) |
| RNF04 | **Conformidade**: sem login automatizado no LinkedIn; sem bypass de CAPTCHA; automação transparente (sem stealth) |
| RNF05 | **Portabilidade**: Python ≥3.11, Docker Compose, dependências pesadas (PDF/Playwright) opcionais via extras |
| RNF06 | **Custo**: scoring em massa usa `cheap_model`; descrição de vagas LinkedIn só é buscada após passar filtros |

---

## 3. Arquitetura

```
┌─────────────────────────── autoapply run ───────────────────────────┐
│                                                                     │
│  APScheduler (N min)          python-telegram-bot (polling)         │
│        │                              │                             │
│        ▼                              ▼                             │
│  ┌──────────────────────── Orchestrator ────────────────────────┐   │
│  │                                                              │   │
│  │  Discovery ─► Matching ─► Tailoring ─► Rendering ─► Decisão  │   │
│  │  (sources)    (LLM cheap)  (LLM main)   (HTML/PDF)    │      │   │
│  │                                              ┌────────┴───┐  │   │
│  │                                              ▼            ▼  │   │
│  │                                          Applier      Notifier  │
│  │                                        (Playwright)  (Telegram) │
│  └──────────────────────────────┬───────────────────────────────┘  │
│                                 ▼                                   │
│                          Tracker (SQLite)                           │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.1 Componentes

| Componente | Módulo | Responsabilidade |
|---|---|---|
| Config | `config.py` | `config.yaml` + `.env`; carrega perfil (resume.json, context.md, answers.yaml) |
| Modelos | `models.py` | `Job`, `MatchResult`, `TailoredApplication`, enums `Mode`/`ApplicationStatus` |
| LLM | `llm.py` | Wrapper litellm; `complete_json()` com retry de parse |
| Discovery | `discovery/*` | 6 conectores plugáveis; filtros de recência/título/remoto na base |
| Matching | `matching.py` | Prompt de recrutador → JSON `{score, reasoning, strengths, gaps}` |
| Tailoring | `tailoring.py` | Prompt com regras de honestidade + `_sanity_check()` programático |
| Rendering | `rendering.py` + `templates/` | Jinja2 → HTML; WeasyPrint → PDF (opcional) |
| Apply | `apply/*` | `ScreeningAnswerer` (LLM + banco de respostas) + appliers Playwright |
| Notify | `notify.py` | Bot API HTTP síncrono; mensagens prontas (alerta, falha, sucesso) |
| Bot | `telegram_bot.py` | Handlers async; aprovação via callback; tailoring on-demand |
| Tracker | `db.py` | SQLite, tabela única `jobs` com ciclo de vida completo |
| CLI | `main.py` | `once` · `run` · `bot` · `tailor <url>` · `status` |

### 3.2 Máquina de estados (por vaga)

```
discovered ─► scored ─┬─► skipped                        (score < alert_threshold)
                      └─► tailored ─┬─► alerted          (mode=alert | fonte s/ automação | score < apply_threshold)
                                    ├─► pending_review ─┬─► approved ─► applied | failed
                                    │                   └─► rejected_by_user
                                    └─► applied | failed (mode=auto)
```
`failed` sempre dispara alerta Telegram com motivo + CV adaptado (RF09).

### 3.3 Matriz de fontes

| Fonte | Descoberta | Aplicação automática | Observação |
|---|---|---|---|
| Greenhouse | API pública de boards | ✅ Playwright | campos padrão + customs obrigatórios |
| Lever | API pública de postings | ✅ Playwright | inclui radios/selects custom |
| Ashby | API pública | ❌ → alerta/review | formulário muito variável |
| Gupy | API do portal (não oficial) | ❌ → alerta/review | exige conta do candidato |
| LinkedIn | listagem pública sem login | ❌ **nunca** | ToS; sempre alerta com CV pronto |
| RSS | feedparser | ❌ → alerta/review | WWR, RemoteOK etc. |

---

## 4. Modelo de dados

### 4.1 Tabela `jobs` (SQLite)

`uid` PK · `source` · `external_id` · `title` · `company` · `location` · `url` · `description` · `posted_at` · `discovered_at` · `status` · `score` · `score_reasoning` · `resume_json` (CV adaptado) · `cover_letter` · `changes_summary` · `pdf_path` · `fail_reason` · `applied_at` · `updated_at`

### 4.2 Perfil do usuário (fonte da verdade)

| Arquivo | Formato | Conteúdo |
|---|---|---|
| `profile/resume.json` | JSON Resume | Currículo base — único lugar de onde fatos podem vir |
| `profile/context.md` | Markdown livre | Narrativa, preferências, pretensão, restrições |
| `profile/answers.yaml` | YAML chave-valor | Respostas canônicas de screening (visto, salário, aviso prévio…) |

---

## 5. Contratos de LLM

Três prompts, todos com saída JSON validada e retry:

1. **Scoring** (`cheap_model`): entrada = vaga + resume + contexto → `{score 0-100, reasoning, strengths[], gaps[]}`. Instrução de realismo (≥80 só com requisitos centrais atendidos).
2. **Tailoring** (`model`): regras inegociáveis (não inventar; só reescrever/reordenar/omitir; skill citada deve existir no perfil) → `{resume, cover_letter ≤200 palavras, changes_summary}`. Pós-validação em código: `basics` restaurados se alterados; experiência inventada ⇒ `ValueError` ⇒ aplicação abortada.
3. **Screening** (`model`): pergunta + opções + banco de respostas → `{answer, confident}`. `confident=false` ⇒ aplicação falha de propósito ⇒ review humano.

---

## 6. Configuração (superfície do usuário)

```yaml
mode: auto | review | alert
llm: {model, cheap_model, temperature}
search: {interval_minutes, titles[], keywords[], locations[], remote_only, max_age_days}
matching: {apply_threshold: 75, alert_threshold: 60}
limits: {max_applications_per_day: 20, min_seconds_between_applications: 300}
sources: {greenhouse.boards[], lever.companies[], ashby.companies[],
          gupy.queries[], linkedin.queries[], rss.feeds[]}   # cada um com enabled
telegram: {enabled}          # token/chat_id via .env
output: {dir, db_path}
```

Segredos somente em `.env`: chaves de LLM + `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

---

## 7. Segurança, ética e conformidade

- Sem stealth/anti-detecção; user-agent identificado; delays educados entre requests.
- CAPTCHA detectado ⇒ falha controlada, nunca bypass.
- LinkedIn: leitura pública apenas, baixa frequência, `can_auto_apply=False` hardcoded.
- Rate-limit próprio: teto diário + intervalo mínimo entre submissões.
- Honestidade do CV garantida em duas camadas (prompt + validação em código).
- Dados pessoais nunca versionados (`.gitignore` cobre perfil, config, DB, saídas).

---

## 8. Critérios de aceite (MVP) — verificados

- [x] `pytest`: 6/6 (UID estável, tracker, rendering, trava de honestidade ×2, defaults de config)
- [x] `compileall` e import de todos os módulos sem erro
- [x] Ciclo completo executável via `autoapply once`; produção via `autoapply run` ou Docker

---

## 9. Roadmap

| Fase | Entrega |
|---|---|
| v0.2 | Applier Ashby; descrição completa Gupy por vaga; screenshot da falha no Telegram |
| v0.3 | Dashboard local (FastAPI/Streamlit) sobre o tracker; export CSV |
| v0.4 | Suporte a Workday/SmartRecruiters; múltiplos templates de CV; CV em 2 idiomas |
| v0.5 | Follow-up automático (detecção de resposta por e-mail) e métricas de conversão por versão de CV |
