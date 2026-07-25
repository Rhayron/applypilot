# Deploy do autocv na Oracle E2.1.Micro (Always Free, 1 GB)

Modelo: **o código vem do GitHub** (`git clone`/`git pull` na VM) e **os segredos
sobem por SCP** do seu PC — porque o `.gitignore` bloqueia (corretamente) `.env`,
`config.yaml` e os arquivos de `profile/`.

Depois da VM criada, o deploy inteiro é **um comando só**.

---

## 1. Criar a instância (única parte manual)

No Console OCI: **Compute → Instances → Create instance**

| Campo | Valor |
|---|---|
| Name | `autocv` |
| Image | **Ubuntu 22.04** |
| Shape | **VM.Standard.E2.1.Micro** (*Always Free eligible*) |
| VCN / Subnet | a VCN criada antes + a **subnet pública** |
| Assign public IPv4 | **Sim** |
| SSH keys | **Generate a key pair for me** → baixe a chave privada |

Guarde a chave baixada (algo como `ssh-key-2026-07-22.key`, normalmente em
`Downloads`) e anote o **Public IP address** quando ficar `RUNNING`.

> Se preferir usar uma chave sua, escolha *Paste public key* e cole o conteúdo de
> `~/.ssh/id_rsa.pub`.

---

## 2. Rodar o deploy

No **PowerShell**, dentro da pasta do projeto:

```powershell
cd C:\Users\Rhayron\Projects\autocv
.\deploy.ps1 -IP SEU_IP -KeyPath "$env:USERPROFILE\Downloads\ssh-key-2026-07-22.key"
```

Se a sua chave for a padrão (`~/.ssh/id_rsa`), pode omitir o `-KeyPath`.

Pronto — é isso. O script faz, em ordem:

1. Confere se os 5 arquivos de segredo/perfil existem e **corrige a permissão da
   chave** (o Windows recusa a chave baixada da Oracle com
   *"UNPROTECTED PRIVATE KEY FILE"* — esse é o erro mais comum aqui).
2. Testa o SSH.
3. Instala **swap de 2 GB + Docker** na VM (via `setup-vm.sh`).
4. Faz `git clone` (ou `git pull` se já existir) de
   `https://github.com/Rhayron/applypilot.git`.
5. Envia por SCP os arquivos que o git não leva:
   `.env`, `config.yaml`, `profile/resume.json`, `profile/context.md`,
   `profile/answers.yaml`.
6. Sobe o container com `docker-compose.vm.yml` (imagem leve, sem Chromium).
7. Mostra status, memória e os últimos logs.

O script é **idempotente**: rode de novo sempre que quiser atualizar. Ele faz
`git pull`, reenvia os segredos e reconstrói.

---

## 3. Conferir se está no ar

O próprio script já imprime o status no fim. Você deve ver o container `Up`, e nos
logs `Scheduler ativo (a cada 180 min)` seguido do primeiro `Ciclo concluído`.

No Telegram, mande `/status` para o bot.

---

## 4. Comandos do dia a dia

```powershell
# logs ao vivo
ssh -i "CAMINHO_DA_CHAVE" ubuntu@SEU_IP "cd autocv && docker compose -f docker-compose.vm.yml logs -f"

# métricas por ciclo
ssh -i "CAMINHO_DA_CHAVE" ubuntu@SEU_IP "cd autocv && docker compose -f docker-compose.vm.yml exec -T autoapply autoapply -c /data/config.yaml metrics"

# reiniciar (após mudar o config.yaml — ele é lido só no boot)
ssh -i "CAMINHO_DA_CHAVE" ubuntu@SEU_IP "cd autocv && docker compose -f docker-compose.vm.yml restart"
```

Para **atualizar o código**: `git push` no seu repo e rode `.\deploy.ps1` de novo.

O `restart: unless-stopped` garante que o container volta sozinho se a VM reiniciar.

---

## 5. Segurança (recomendado)

Na **security list** da subnet, troque a origem da regra de ingress da porta 22 de
`0.0.0.0/0` para `SEU_IP/32`. Descubra seu IP em https://ifconfig.me

Nenhuma outra porta precisa ficar aberta: o bot do Telegram usa polling (conexão de
saída) e o Gemini também é chamada de saída.

---

## Notas

- **Commite os arquivos de deploy.** `Dockerfile.light`, `docker-compose.vm.yml`,
  `setup-vm.sh`, `deploy.ps1` e este `DEPLOY.md` não são segredo e deveriam estar no
  repo. Enquanto não estiverem, o script os envia por SCP como garantia, então o
  deploy funciona de qualquer forma:
  ```powershell
  git add Dockerfile.light docker-compose.vm.yml setup-vm.sh deploy.ps1 DEPLOY.md
  git commit -m "Adiciona deploy automatizado para VM Oracle"
  git push
  ```
- **Sem auto-apply**: a imagem leve não inclui Playwright/Chromium (300–500 MB de
  RAM só ele). No modo `review` isso quase não muda nada — você recebe o CV
  adaptado no Telegram e aplica manualmente. Para auto-apply, migre para a A1 e use
  o `Dockerfile` normal.
- **Banco**: o `autoapply.db` fica na VM e não é sobrescrito pelo deploy. O
  histórico de vagas e de ciclos é preservado entre atualizações.
