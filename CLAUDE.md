# AutoApply — guia de operação

Agente que busca vagas, adapta o currículo do Rhayron e o ajuda a se candidatar.
Roda **sozinho** na VPS `179.198.96.172`, em `~/autocv`, no container
`autocv-autoapply-1`.

Você provavelmente está aqui para operar ou consertar. Leia os invariantes antes de
mudar comportamento.

## Invariantes

1. **Nunca invente nada no currículo.** Nenhuma empresa, tecnologia, número,
   certificação ou período que não esteja no `profile/base.docx`. Há validação
   (`tailoring._sanity_check` e `docx_resume.validar`); não a contorne.
2. **O currículo é uma EDIÇÃO do `profile/base.docx`**, nunca um documento novo.
   Gerar do zero descarta formatação, seções e estilos do arquivo real dele.
3. **`apply_job` envia candidatura de verdade.** Só com aprovação explícita do
   Rhayron, vaga a vaga.
4. **`telegram.bot: true` exige bot exclusivo.** Dois processos no mesmo token dão
   `Conflict` e derrubam um deles. O bot do autopilot é `@AutopilotSN_bot`; o do
   Hermes é outro.
5. **`out/_antigos/`** guarda currículos do template velho. Não use como referência.

## O fluxo

```
scheduler (180 min) ─→ descobre ─→ pontua ─┬─ score < alert_threshold → skipped
                                           │
                                           └─ pending_generation   ← PARA aqui
                                                  │
                       [🤖 Claude] [⚡ Gemini] [❌ Descartar]   (botões no Telegram)
                                                  │
                                             gerar_cv(uid, editor)
                                                  │
                                    ┌─────────────┴─────────────┐
                              pending_review                 alerted
                            (greenhouse, lever)         (resto: envio manual)
                                    │
                          [✅ Aplicar] [❌ Ignorar]
                                    │
                               apply_job(uid)
```

O ciclo **não gera currículo sozinho** — só em `mode: auto`. Gerar custa uma chamada
cara de LLM e produz documento em nome do usuário.

## Mapa do código

| Arquivo | Papel |
|---|---|
| `main.py` | CLI: `run`, `once`, `mcp`, `pending`, `status`, `metrics`, `tailor` |
| `orchestrator.py` | ciclo, `gerar_cv`, `apply_by_uid`, recarga de config, lock entre processos |
| `docx_resume.py` | adapta o `.docx`: Claude CLI primeiro, Gemini de reserva, validação |
| `discovery/base.py` | `_passes_filters` — idade, título/palavra, local, modalidade |
| `rendering.py` | `docx_para_pdf` (LibreOffice) e o template antigo (fallback) |
| `telegram_bot.py` | bot próprio: botões dos dois portões, `/filtros`, `/help` |
| `notify.py` | mensagens; `interactive` liga os botões |
| `mcp_server.py` | 13 ferramentas para o Hermes, com anotações de natureza |
| `idioma.py` | detecta pt/en na vaga; en faz traduzir o currículo inteiro |
| `config.py` | `SETTABLE` + `aplicar_mudancas` (whitelist compartilhada bot/MCP) |

## Operação

```bash
cd ~/autocv
docker compose logs -f                       # acompanhar
docker compose build -q && docker compose up -d   # deploy após git pull
docker exec autocv-autoapply-1 autoapply -c /data/config.yaml status
docker exec autocv-autoapply-1 autoapply -c /data/config.yaml pending
```

**Trocar credencial no `.env` exige `docker compose up -d --force-recreate`.**
O Compose injeta o `env_file` na criação do container e o `load_dotenv()` não
sobrescreve variável já existente — com `restart` o valor antigo continua valendo.
Isso já custou um diagnóstico errado.

Config muda a quente: o ciclo relê o `config.yaml` quando o mtime muda. Não precisa
reiniciar.

## Filtros

Uma vaga entra se o **título** casa com `titles`, **ou** se título/descrição casam
com `keywords`. Depois passa por local, modalidade e idade.

- `titles` — nome do cargo.
- `keywords` — termo de nicho, casa também na **descrição**. É o que faz "firmware
  embarcado" funcionar quando o título é só "Software Engineer".
- `modalidade` — `remoto` | `presencial` | `ambos`. Vaga que não informa **sempre
  passa**: a maioria das fontes omite.
- `presencial_em` — cidades onde presencial serve mesmo com `modalidade: remoto`.
  Configuração atual: remoto em qualquer lugar + presencial em Curitiba, com
  `locations` vazio (preenchê-lo cortaria as remotas de fora).

Pelo Telegram: `/filtros` mostra, `/help` explica cada comando.

## Armadilhas já pagas

- **Dedupe é por conteúdo, não só por ID.** `uid = sha256(source:external_id)`, e o
  LinkedIn republica a mesma vaga com ID novo. `Job.dedupe_key` (título+empresa
  normalizados) é o que impede alerta e adaptação duplicados.
- **Sem `max_tokens`.** Gemini 3+ gasta tokens de raciocínio no mesmo orçamento da
  resposta; teto apertado devolvia `content` vazio, que virava JSON inválido.
- **Gemini 3+ depreciou `temperature`.** `llm.sampling_via_prompt` detecta e move a
  orientação para o `system`.
- **Nome de arquivo precisa de `sanitizar_slug`.** Título com barra ("AI/ML
  Engineer") virava diretório.
- **Travessão:** só acusa quando a edição o *introduziu* e fora de intervalo de
  datas. O currículo escreve "Mar. 2023 – Present" legitimamente.
- **`telegram.enabled` (envia) e `telegram.bot` (escuta) são coisas separadas.**
- **Todo módulo precisa ser importável no teste.** `telegram_bot` e `mcp_server` não
  eram cobertos, e um `SyntaxError` neles derrubou o container em produção. Há um
  teste que importa o pacote inteiro; mantenha-o.

## Hermes (opcional)

O Hermes é outro agente na mesma VPS e enxerga o autopilot pelo MCP `autopilot`. Ele
**não é necessário** — o autopilot roda sozinho. Quando o Rhayron pede, ele usa as
ferramentas; a skill `autoapply-autopilot` o proíbe de gerar currículo por conta
própria, que foi o motivo de o autopilot voltar a ser autônomo.

Detalhes do contrato: `hermes/HANDOFF.md`.

## Testes

```bash
python -m pytest tests/ -q      # 33 testes, sem rede nem LLM
```
