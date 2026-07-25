# ============================================================================
#  deploy.ps1 — Deploy automatizado do autocv na VM Oracle (Ubuntu).
#
#  O código vem do GitHub (git clone/pull). Os segredos e o perfil, que o
#  .gitignore bloqueia (com razão), sobem por SCP a partir deste PC.
#
#  Uso no PowerShell:
#     .\deploy.ps1 -IP 123.45.67.89 -KeyPath "$env:USERPROFILE\Downloads\ssh-key-2026-07-22.key"
#
#  Rode de novo sempre que quiser atualizar — é idempotente.
# ============================================================================
param(
  [Parameter(Mandatory = $true)][string]$IP,
  [string]$KeyPath   = "$env:USERPROFILE\.ssh\id_rsa",
  [string]$User      = "ubuntu",
  [string]$Repo      = "https://github.com/Rhayron/applypilot.git",
  [string]$RemoteDir = "autocv"
)

$ErrorActionPreference = "Stop"
$Local = $PSScriptRoot
$Remote = "$User@$IP"

function Step($n, $msg) { Write-Host "`n==> [$n] $msg" -ForegroundColor Cyan }
function Ok($msg)        { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg)      { Write-Host "    $msg" -ForegroundColor Yellow }
function Die($msg)       { Write-Host "ERRO: $msg" -ForegroundColor Red; exit 1 }

# Arquivos que o .gitignore bloqueia e que a VM precisa ter.
$Secrets = @(
  ".env",
  "config.yaml",
  "profile\resume.json",
  "profile\context.md",
  "profile\answers.yaml"
)
# Arquivos de deploy: idealmente vêm do git, mas subimos como garantia.
$DeployFiles = @("Dockerfile.light", "docker-compose.vm.yml", "setup-vm.sh")

# --------------------------------------------------------------------------
Step 1 "Verificando arquivos locais e a chave SSH"

if (-not (Test-Path $KeyPath)) { Die "Chave SSH não encontrada em: $KeyPath" }

foreach ($f in $Secrets) {
  if (-not (Test-Path (Join-Path $Local $f))) { Die "Arquivo obrigatório não encontrado: $f" }
}
Ok "Todos os $($Secrets.Count) arquivos de segredo/perfil encontrados."

# Windows recusa chaves com permissão aberta ("UNPROTECTED PRIVATE KEY FILE").
# Isso acontece sempre com a chave que a Oracle deixa você baixar.
icacls $KeyPath /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
Ok "Permissões da chave ajustadas (evita 'UNPROTECTED PRIVATE KEY FILE')."

$SSH = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15")

# --------------------------------------------------------------------------
Step 2 "Testando conexão SSH com $Remote"
$test = & ssh @SSH $Remote "echo CONECTADO" 2>&1
if ($LASTEXITCODE -ne 0 -or "$test" -notmatch "CONECTADO") {
  Die "Não consegui conectar. Verifique o IP, a chave e se a porta 22 está liberada na security list.`n$test"
}
Ok "Conexão OK."

# --------------------------------------------------------------------------
Step 3 "Preparando a VM (swap de 2 GB + Docker)"
& scp @SSH (Join-Path $Local "setup-vm.sh") "${Remote}:~/setup-vm.sh" | Out-Null
if ($LASTEXITCODE -ne 0) { Die "Falha ao enviar setup-vm.sh" }

& ssh @SSH $Remote "chmod +x ~/setup-vm.sh && ~/setup-vm.sh"
if ($LASTEXITCODE -ne 0) { Die "Falha ao preparar a VM." }
Ok "Swap e Docker prontos."

# --------------------------------------------------------------------------
Step 4 "Clonando/atualizando o código do GitHub na VM"
$gitCmd = "sudo apt-get install -y git >/dev/null 2>&1; " +
          "if [ -d ~/$RemoteDir/.git ]; then cd ~/$RemoteDir && git pull --ff-only; " +
          "else git clone $Repo ~/$RemoteDir; fi"
& ssh @SSH $Remote $gitCmd
if ($LASTEXITCODE -ne 0) { Die "Falha no git clone/pull." }
Ok "Código atualizado em ~/$RemoteDir"

# --------------------------------------------------------------------------
Step 5 "Enviando segredos e perfil (não vão pelo git)"
& ssh @SSH $Remote "mkdir -p ~/$RemoteDir/profile"
foreach ($f in $Secrets) {
  $src = Join-Path $Local $f
  $dst = $f -replace '\\', '/'
  & scp @SSH $src "${Remote}:~/$RemoteDir/$dst" | Out-Null
  if ($LASTEXITCODE -ne 0) { Die "Falha ao enviar $f" }
  Ok "enviado: $dst"
}

# Garante os arquivos de deploy mesmo que ainda não estejam commitados.
foreach ($f in $DeployFiles) {
  $src = Join-Path $Local $f
  if (Test-Path $src) { & scp @SSH $src "${Remote}:~/$RemoteDir/$f" | Out-Null }
}
Ok "Arquivos de deploy sincronizados."

# --------------------------------------------------------------------------
Step 6 "Subindo o container (build pode demorar alguns minutos na micro)"
& ssh @SSH $Remote "cd ~/$RemoteDir && docker compose -f docker-compose.vm.yml up -d --build"
if ($LASTEXITCODE -ne 0) { Die "Falha no docker compose up." }
Ok "Container no ar."

# --------------------------------------------------------------------------
Step 7 "Verificação"
& ssh @SSH $Remote "cd ~/$RemoteDir && docker compose -f docker-compose.vm.yml ps && echo '--- memória ---' && free -h && echo '--- últimos logs ---' && docker compose -f docker-compose.vm.yml logs --tail 25"

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host " DEPLOY CONCLUÍDO" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Acompanhar logs:"
Write-Host "   ssh -i `"$KeyPath`" $Remote `"cd $RemoteDir && docker compose -f docker-compose.vm.yml logs -f`""
Write-Host " Métricas por ciclo:"
Write-Host "   ssh -i `"$KeyPath`" $Remote `"cd $RemoteDir && docker compose -f docker-compose.vm.yml exec -T autoapply autoapply -c /data/config.yaml metrics`""
Write-Host " Reimplantar (após mudanças): rode este script de novo."
Write-Host ""
