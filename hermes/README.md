# Hermes como maestro do autopilot

O autopilot não fala mais com você direto. Quem conversa é o **Hermes**, que roda na
mesma VPS e ganhou o autopilot como um conjunto de ferramentas MCP.

```
                      ┌── Hermes (gemini-3.6-flash) ── conversa, decide, comanda
você <--Telegram--────┤        |
   @rhayronsnbot      │        | MCP stdio: docker exec -i autocv-autoapply-1 autoapply mcp
   (uma identidade)   │        v
                      └── autopilot ── alerta de vaga + PDF (sendMessage/sendDocument)
                               |
                               +-- litellm --> Gemini (scoring + adaptação de CV)
                               +-- SQLite (autoapply.db) + out/*.pdf
```

Os dois escrevem pelo **mesmo bot** (`@rhayronsnbot`, id `8815061521`), então tudo
chega numa conversa só. O autopilot avisa na hora que acha a vaga, com o CV anexado;
o Hermes responde quando você fala.

**Divisão de trabalho:** o Hermes decide e narra; o Gemini direto continua fazendo o
trabalho pesado por vaga. Pontuar dezenas de vagas por ciclo com um agente completo
custaria muito mais e entregaria o mesmo modelo — o Hermes também roda Gemini.

## As peças

| Onde | O quê |
|---|---|
| `autoapply/mcp_server.py` | 11 ferramentas MCP via stdio |
| `autoapply mcp` | sobe o servidor; é o que o Hermes executa |
| `~/hermes-data/config.yaml` → `mcp_servers.autopilot` | registro no Hermes |
| `.env` do autopilot → `TELEGRAM_BOT_TOKEN` | token do bot do **Hermes** |
| `hermes/autopilot_watch.py` | watcher de reserva, hoje **não instalado** |

O watcher existia para anunciar a fila a cada 30 min, quando o autopilot estava mudo.
Desde que ele voltou a alertar na hora pelo bot do Hermes, o cron foi removido —
manteria uma segunda mensagem para a mesma vaga. O script fica versionado aqui caso
você queira um digest periódico depois.

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

## Enviar sim, escutar não

São dois flags separados no `config.yaml`, e a distinção é o que evita quebrar tudo:

```yaml
telegram:
  enabled: true    # manda alertas (sendMessage / sendDocument)
  bot: false       # roda o bot próprio com polling e botões
```

Quem faz `getUpdates` naquele token é o gateway do Hermes. Se o autopilot também
fizesse polling, a Bot API derrubaria um dos dois com
`Conflict: terminated by other getUpdates request`. Por isso **`bot` tem que
continuar `false`** — enviar é seguro, escutar não.

Como consequência, os botões inline de aprovar/ignorar não existem mais: ninguém
trataria o callback. A mensagem traz o `uid` e você responde ao Hermes em linguagem
natural ("aplica a da Nubank", "descarta essa").

## Operação

```bash
ssh hermes-vps 'docker exec hermes hermes mcp test autopilot'   # valida a conexão
ssh hermes-vps 'docker exec hermes hermes cron list'            # jobs agendados
ssh hermes-vps 'cd autocv && docker compose logs -f'            # logs do autopilot
```
