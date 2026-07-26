---
name: autoapply-autopilot
description: "Fluxo obrigatório do AutoApply/autocv: buscar vagas, gerar currículo adaptado e candidatar-se. Use SEMPRE que a conversa envolver autopilot, autocv, vaga, emprego, currículo, CV, candidatura, aplicar em vaga, ajustar filtros de busca, ou disparar/agendar o ciclo. Também ao receber notificação de vaga nova no Telegram."
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [autopilot, autocv, vagas, emprego, curriculo, cv, candidatura, mcp, cron]
---

# AutoApply — fluxo obrigatório

O AutoApply roda no container `autocv-autoapply-1` e é exposto a você pelo servidor
MCP **`autopilot`**. Ele é a única autoridade sobre currículo e candidatura do Rhayron.

## Você age só quando pedirem

O autopilot é **autônomo**: tem scheduler próprio, bot próprio no Telegram
(`@rhayron_autocv_bot`, separado do seu) e botões próprios para o Rhayron decidir
gerar currículo e aprovar candidatura. Ele não precisa de você para funcionar.

Portanto:
- **Nunca aja por iniciativa própria** sobre vagas. Não rode ciclo, não gere
  currículo, não aplique porque achou oportuno.
- **Não anuncie vagas novas** no seu chat: o bot do autopilot já faz isso, com os
  botões. Duplicar só confunde.
- Aja quando o Rhayron **pedir explicitamente a você** — "usa o autopilot para…",
  "me mostra a fila", "roda o ciclo agora". Aí use as ferramentas normalmente.

## A regra que não se quebra

**NUNCA escreva, monte, edite ou traduza um currículo por conta própria.** Não use
suas ferramentas de arquivo, não gere .docx nem PDF, não redija texto de currículo no
chat como se fosse o documento final.

O motivo é concreto: o autopilot **edita o .docx real do Rhayron**
(`profile/base.docx`) preservando formatação, seções, estilos e numeração. Um
documento criado do zero perde tudo isso e entrega um currículo que não é o dele — foi
exatamente o erro cometido antes desta skill existir.

Se você acha que precisa criar um arquivo de currículo, você está no caminho errado.
Chame `gerar_cv`.

## O fluxo, em dois portões de decisão

```
ciclo acha a vaga → pontua → PARA em pending_generation
   │
   └─ [1] "gero o currículo? com claude ou gemini?"  ← decisão do Rhayron
        │
        gerar_cv(uid, editor) → PDF vai sozinho para o chat
        │
        └─ [2] "aplico nesta vaga?"                  ← decisão do Rhayron
             │
             apply_job(uid)   (só se auto_aplicavel=true)
```

O ciclo **não** gera currículo sozinho. Ele para e espera. Isso é intencional: gerar
custa uma chamada cara de LLM e produz um documento em nome dele.

## Como agir em cada situação

**"o que tem na fila?" / notificação de vaga nova / início de conversa sobre vagas**
→ `awaiting_decision()`. Reporte agrupando pelo campo `espera`:
- `gerar_cv` — passou no corte, currículo ainda não existe. Pergunte se quer gerar e
  com qual editor.
- `aplicar` — currículo pronto e a fonte aceita envio automático. Pergunte se aplica.
- `envio_manual` — currículo pronto, mas a fonte não tem automação. Entregue o link e
  o PDF para ele se candidatar à mão. **É a maioria dos casos.**

**"gera o currículo dessa vaga"**
→ Se ele não disse o editor, pergunte antes. A diferença importa:
- `claude` — texto melhor e mais alinhado ao vocabulário da vaga, ~60 a 90s. Reescreve
  com mais liberdade e às vezes encurta bullets.
- `gemini` — ~25s, mais conservador: preserva mais do texto original.
- `auto` — tenta Claude, cai para Gemini se falhar.
→ Depois chame `gerar_cv(uid, editor)`. O PDF é enviado ao chat pelo próprio autopilot;
não tente anexar arquivo você mesmo.

**"aplica nessa vaga"**
→ `apply_job(uid)` é **irreversível**: manda a candidatura de verdade, no nome dele.
Só chame com aprovação explícita para **aquela** vaga. Aprovação para uma não vale
para outra. Só funciona com `auto_aplicavel=true`; nas demais, oriente o envio manual.

**"procure vagas só em Curitiba" / "quero vagas de firmware embarcado"**
→ `ajustar_busca()`. Ela soma e remove itens, você não precisa reenviar a lista toda.
- `adicionar_titulos` — o que aparece no **nome do cargo**.
- `adicionar_palavras` — termo de nicho que aparece no **corpo** do anúncio.
- `locais` — substitui a lista; `[]` remove o filtro.
Ao atender pedido amplo ("e afins"), gere você mesmo as variações, inclusive em
inglês: boa parte das fontes publica assim. Avise que o filtro vale para descobertas
novas e não reavalia vaga já vista.

**"roda o ciclo agora" / disparar o cron**
→ `run_cycle()`. Leva ~30 a 60s e devolve estatísticas. Se responder `ocupado`, o
scheduler já está rodando um ciclo; espere e tente de novo. Depois do ciclo, chame
`awaiting_decision()` para ver se apareceu vaga nova.

**"muda o threshold" / "de quanto em quanto tempo roda"**
→ `get_config()` e `set_config()`. Só a whitelist é aceita; o resto é recusado de
propósito. Vale a partir do próximo ciclo.

## Ferramentas

Leitura, à vontade: `status`, `metrics`, `list_jobs`, `awaiting_decision`,
`job_detail`, `get_config`.

Escrita, com intenção declarada pelo Rhayron: `run_cycle`, `gerar_cv`, `tailor_url`,
`set_config`, `ajustar_busca`, `reject_job`.

Irreversível, só com aprovação explícita e específica: **`apply_job`**.

## Limites que você deve respeitar e explicar

- **Nunca invente experiência, tecnologia, número ou certificação.** O autopilot tem
  validação contra isso; não tente contorná-la reescrevendo texto por fora.
- **Só greenhouse e lever** têm envio automático. LinkedIn, Gupy, Ashby e RSS geram o
  currículo, mas a candidatura é manual. Diga isso claramente em vez de deixar ele
  achar que você aplicou.
- **Vaga em inglês** faz o autopilot traduzir o currículo inteiro sozinho. Não traduza
  nada por fora.
- Se `gerar_cv` devolver um resumo começando com "⚠️ ATENÇÃO", a edição do .docx
  falhou e o currículo foi montado do zero, **sem a formatação do arquivo dele**.
  Avise o Rhayron de forma destacada e sugira gerar de novo com o outro editor.
