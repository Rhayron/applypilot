#!/usr/bin/env bash
# ============================================================================
# setup-vm.sh — prepara a VM Ubuntu (Oracle E2.1.Micro, 1 GB) para o autocv.
# Faz: swap de 2 GB + Docker + Docker Compose.
#
# Rode NA VM:   chmod +x setup-vm.sh && ./setup-vm.sh
# ============================================================================
set -euo pipefail

echo "==> [1/3] Swap de 2 GB (essencial em 1 GB de RAM)"
if swapon --show | grep -q '/swapfile'; then
  echo "    swap já configurado, pulando."
else
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  # prioriza RAM; só usa swap quando apertar de verdade
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf >/dev/null
  sudo sysctl -p /etc/sysctl.d/99-swap.conf >/dev/null
  echo "    swap criado."
fi
free -h

echo
echo "==> [2/3] Docker"
if command -v docker >/dev/null 2>&1; then
  echo "    docker já instalado, pulando."
else
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                          docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
  sudo systemctl enable --now docker
  echo "    docker instalado."
fi

echo
echo "==> [3/3] Verificação"
sudo docker --version
sudo docker compose version

echo
echo "============================================================"
echo "Pronto. IMPORTANTE: saia do SSH e entre de novo (ou rode"
echo "'newgrp docker') para usar o docker sem sudo."
echo "============================================================"
