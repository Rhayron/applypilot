# Hermes como maestro do autopilot

O autopilot não fala mais com você direto. Quem conversa é o **Hermes**, que roda na
mesma VPS e ganhou o autopilot como um conjunto de ferramentas MCP.

```
você  <--Telegram-->  Hermes (gemini-3.6-flash)
                         |
                         | MCP stdio: docker exec -i autocv-autoapply-1 autoapply mcp
                         v
                      autopilot  --litellm-->  Gemini (scoring + adaptação de CV)
                         |
                         +-- SQLite (autoapply.db) + out/*.pdf
```

**Divisão de trabalho:** o Hermes decide e narra; o Gemini direto continua fazendo o
trabalho pesado por vaga. Pontuar dezenas de vagas por ciclo com um agente completo
custaria muito mais e entregaria o mesmo modelo — o Hermes também roda Gemini.

## As peças

| Onde | O quê |
|---|---|
| `autoapply/mcp_server.py` | 11 ferramentas MCP via stdio |
| `autoapply mcp` | sobe o servidor; é o que o Hermes executa |
| `hermes/autopilot_watch.py` | job de cron que anuncia vagas novas no chat |
| `~/hermes-data/config.yaml` → `mcp_servers.autopilot` | registro no Hermes |
| `~/hermes-data/scripts/autopilot_watch.py` | o watcher instalado |
| `~/hermes-data/autopilot_seen.json` | uids já anunciados (evita repetir) |

## Ferramentas expostas

Leitura: `status`, `metrics`, `list_jobs`, `awaiting_decision`, `job_detail`,
`get_config`.
Ação: `run_cycle`, `tailor_url`, `set_config`, `reject_job`, `apply_job`.

Duas travas importantes:

- **`set_config` tem allowlist.** Só os campos em `SETTABLE` (thresholds, termos de
  busca, intervalo, limites, modo). Caminhos de perfil, banco e credenciais estão
  fora do alcance do agente, e o config é validado antes de gravar.
- **`apply_job` é irreversível e exige aprovação sua**, vaga a vaga. Só funciona em
  vagas com `auto_aplicavel: true`.

## `auto_aplicavel`: a pegadinha

Só **greenhouse** e **lever** têm automação de envio. Vaga de linkedin, gupy ou RSS
termina em status `alerted`, não `pending_review` — o CV é gerado, mas o envio é seu.
Por isso a fila que importa é `awaiting_decision()`, que junta os dois status. Filtrar
só por `pending_review` esconde a maioria das vagas.

## Ajuste em tempo real

O `run_cycle` relê o `config.yaml` antes de rodar, então `set_config` vale já no
próximo ciclo — sem reiniciar container. Para valer na hora, peça um ciclo em seguida.

Um lock de arquivo (`.cycle.lock`) impede que o ciclo pedido pelo Hermes atropele o
ciclo do scheduler: são processos separados no mesmo container.

## Operação

```bash
ssh hermes-vps 'docker exec hermes hermes mcp test autopilot'   # valida a conexão
ssh hermes-vps 'docker exec hermes hermes cron list'            # jobs agendados
ssh hermes-vps 'docker exec hermes python3 /opt/data/scripts/autopilot_watch.py'
```

O watcher roda a cada 30 min (`*/30 * * * *`) e é silencioso por padrão: sem vaga
nova, sem mensagem.

Para reinstalar o watcher depois de mudá-lo aqui:

```bash
scp hermes/autopilot_watch.py hermes-vps:/root/hermes-data/scripts/
```

## O bot antigo

`telegram.enabled: false` no `config.yaml` do autopilot. Isso cala tanto o bot quanto
os alertas — antes o flag só desligava o bot e o token do `.env` mantinha as DMs
saindo pela identidade antiga, o que causava `telegram.error.Conflict` quando duas
instâncias faziam polling do mesmo token.
