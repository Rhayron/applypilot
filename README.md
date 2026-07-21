# ApplyPilot 🤖

> Pacote e CLI se chamam `autoapply`.

Agente LLM que **busca vagas periodicamente**, **adapta seu currículo dinamicamente** para cada vaga (sem inventar nada) e **aplica automaticamente** — ou te avisa pelo **Telegram** com o CV pronto quando não consegue.

## Como funciona

```
Scheduler (a cada N min)
   └─> Discovery: Greenhouse · Lever · Ashby · Gupy · LinkedIn(alerta) · RSS
        └─> Matching: LLM pontua fit 0-100 (modelo barato)
             └─> score >= alert_threshold?
                  └─> Tailoring: LLM adapta o JSON Resume + cover letter
                       └─> Render: HTML + PDF ATS-friendly
                            ├─ mode=auto   → aplica via Playwright (Greenhouse/Lever)
                            │                falhou? → Telegram c/ vaga + CV + motivo
                            ├─ mode=review → Telegram c/ botões ✅ Aplicar / ❌ Ignorar
                            └─ mode=alert  → Telegram c/ vaga + CV (nunca aplica)
```

Multi-provider via [litellm]: **Anthropic, OpenAI, Gemini, xAI/Grok, OpenRouter** — troque só o `llm.model` no config.

## Setup

```bash
git clone https://github.com/Rhayron/applypilot.git && cd applypilot
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[pdf,apply]"
playwright install chromium

cp .env.example .env                          # preencha as chaves
cp config.example.yaml config.yaml            # ajuste buscas/modo
cp profile/resume.example.json profile/resume.json      # SEU currículo real
cp profile/context.example.md profile/context.md        # seu contexto
cp profile/answers.example.yaml profile/answers.yaml    # respostas de screening
```

### Telegram
1. Crie um bot com o [@BotFather](https://t.me/botfather) → copie o token para `TELEGRAM_BOT_TOKEN`.
2. Rode `autoapply bot`, mande `/start` para o bot — ele mostra seu `chat_id`.
3. Coloque o `chat_id` em `TELEGRAM_CHAT_ID` no `.env`.

## Uso

```bash
autoapply once            # um ciclo agora (bom para testar)
autoapply run             # produção: scheduler + bot Telegram
autoapply bot             # só o bot (aprovações + tailoring on-demand)
autoapply tailor <url>    # adapta o CV para uma vaga específica
autoapply status          # estatísticas (vagas por status)
autoapply metrics         # histórico de métricas por ciclo de busca
```

Ou com Docker:

```bash
docker compose up -d --build
```

### Modos (`mode:` no config.yaml)
| Modo | Comportamento |
|---|---|
| `auto` | Aplica sozinho (score ≥ `apply_threshold`); Telegram só em sucesso/falha |
| `review` | Envia vaga + CV no Telegram e espera você apertar ✅ antes de aplicar |
| `alert` | Nunca aplica; só envia vaga + CV adaptado |

Você também pode mandar **qualquer link de vaga no chat do bot** e receber o CV adaptado na hora.

## Garantias de honestidade

O prompt de tailoring proíbe inventar experiências/números/skills, e o código
(`tailoring._sanity_check`) **aborta a aplicação** se o LLM alterar empresas,
datas ou dados de contato. Ele só reescreve, reordena e omite.

## Avisos importantes

- **LinkedIn**: o ToS proíbe scraping/automação. O conector usa apenas a listagem
  pública sem login e **nunca aplica automaticamente** — vagas do LinkedIn sempre
  chegam como alerta com o CV pronto para você aplicar manualmente. Use por sua conta e risco.
- **CAPTCHA / perguntas sem resposta confiável**: a aplicação automática falha de
  propósito e vira alerta no Telegram (human-in-the-loop). O sistema não burla CAPTCHAs.
- Limites de segurança em `limits:` (máx/dia e intervalo entre aplicações).

## Estrutura

```
autoapply/
├── config.py         # config.yaml + .env
├── models.py         # Job, MatchResult, TailoredApplication
├── db.py             # tracker SQLite
├── llm.py            # wrapper multi-provider (litellm)
├── matching.py       # scoring de fit
├── tailoring.py      # adaptação do CV + trava de honestidade
├── rendering.py      # HTML/PDF ATS-friendly
├── notify.py         # alertas Telegram
├── orchestrator.py   # pipeline + limites
├── telegram_bot.py   # bot interativo (aprovar/rejeitar, tailor on-demand)
├── main.py           # CLI
├── discovery/        # greenhouse, lever, ashby, gupy, linkedin, rss
└── apply/            # appliers Playwright (greenhouse, lever)
```

## Estendendo

- **Nova fonte de vagas**: subclasse de `discovery.base.JobSource`, registre em `discovery/__init__.py`.
- **Novo ATS com aplicação automática**: subclasse de `apply.base.Applier`, registre em `apply/__init__.py`.

## Testes

```bash
pip install -e ".[dev]" && pytest
```
