# Handoff: AutoApply ↔ Hermes

Documento de referência do contrato entre os dois sistemas. A versão operacional, que
o Hermes carrega em runtime, é a skill `autopilot` (`SKILL-autopilot.md`, instalada em
`~/hermes-data/skills/autopilot/SKILL.md` na VPS).

## Quem faz o quê

| | Autopilot | Hermes |
|---|---|---|
| Descobrir e pontuar vagas | ✅ | — |
| Editar o `.docx` e gerar PDF | ✅ | nunca |
| Decidir *quando* gerar | — | pergunta ao usuário |
| Conversar, narrar, ajustar filtros | — | ✅ |
| Enviar candidatura | ✅ executa | ✅ pede aprovação |

O Hermes é o maestro: decide e narra. O autopilot é quem age sobre currículo e vaga.
A divisão existe porque o autopilot edita o **documento real** do usuário; qualquer
currículo criado fora dele perde formatação, seções e estilos.

## Máquina de estados

```
discovered → scored ─┬─ (score < alert_threshold) → skipped
                     │
                     └─ pending_generation      ← ciclo PARA aqui
                              │ gerar_cv(uid, editor)
                              ▼
                          tailored ─┬─ pending_review   (greenhouse, lever)
                                    │        │ apply_job(uid)
                                    │        ▼
                                    │     applied | failed
                                    └─ alerted            (envio manual)

                          reject_job(uid) → rejected_by_user
```

Em `mode: auto` o ciclo não para: gera e aplica direto. Em `review` (o atual) e
`alert`, para em `pending_generation`.

## Superfície MCP

Servidor `autopilot`, transporte stdio:

```yaml
mcp_servers:
  autopilot:
    command: docker
    args: [exec, -i, autocv-autoapply-1, autoapply, -c, /data/config.yaml, mcp]
    enabled: true
```

Funciona porque o container do Hermes tem o socket do Docker montado. As 13
ferramentas declaram sua natureza via anotações MCP (`readOnlyHint`,
`destructiveHint`), para o cliente saber o que exige confirmação.

| Ferramenta | Natureza |
|---|---|
| `status`, `metrics`, `list_jobs`, `awaiting_decision`, `job_detail`, `get_config` | leitura |
| `run_cycle`, `gerar_cv`, `tailor_url`, `set_config`, `ajustar_busca`, `reject_job` | escrita |
| `apply_job` | **irreversível** |

## Invariantes

1. **O Hermes nunca produz currículo.** Nem arquivo, nem texto que faça as vezes dele.
2. **`apply_job` exige aprovação explícita por vaga.** Uma aprovação não se estende à
   próxima.
3. **`set_config` tem whitelist.** Caminhos de perfil, banco e credenciais estão fora
   do alcance do agente, e o config é validado antes de gravar.
4. **Nada de experiência inventada.** Validação no autopilot; não contornar por fora.
5. **`telegram.bot` fica `false`.** Enviar pelo token do Hermes é seguro; fazer polling
   nele briga com o gateway e derruba um dos dois.

## Notificação

O autopilot escreve pelo bot do Hermes (`@rhayronsnbot`), então tudo chega numa
conversa só. Ele envia; quem escuta é o gateway do Hermes. Por isso a notificação de
vaga nova não traz botão inline: os botões aparecem quando o Hermes vai executar a
ação.

## Onde as coisas ficam

| Caminho | O quê |
|---|---|
| `~/autocv/` (VPS) | código, `config.yaml`, `autoapply.db`, `out/` |
| `~/autocv/profile/base.docx` | o currículo real, ponto de partida de toda adaptação |
| `~/hermes-data/skills/autopilot/SKILL.md` | a skill que obriga o fluxo |
| `~/hermes-data/memories/MEMORY.md` | entrada curta apontando para o mesmo contrato |
| `~/hermes-data/config.yaml` → `mcp_servers.autopilot` | registro do servidor |
