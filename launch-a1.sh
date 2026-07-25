#!/usr/bin/env bash
# ============================================================================
# launch-a1.sh — tenta criar a VM A1.Flex (Always Free) até cair vaga.
#
# Auto-configurável. No OCI Cloud Shell, basta:
#     chmod +x launch-a1.sh && ./launch-a1.sh
#
# Resolve sozinho: compartment, subnet pública, Availability Domains,
# imagem Ubuntu 22.04 ARM, chave SSH e metadata. Não precisa editar nada.
#
# Dica: rode dentro do tmux pra sobreviver a desconexões:
#     tmux new -s a1     (Ctrl+b depois d = sai;  tmux attach -t a1 = volta)
# ============================================================================
set -uo pipefail

# ----------------------- OPCIONAL: só edite se quiser ----------------------
COMPARTMENT_ID=""                          # vazio = $OCI_TENANCY
SUBNET_ID=""                               # vazio = 1ª subnet pública
IMAGE_ID=""                                # vazio = Ubuntu 22.04 ARM recente
AD_LIST=()                                 # vazio = todas as ADs da região
SSH_PUBKEY_FILE="$HOME/.ssh/id_rsa.pub"    # gerada se não existir
DISPLAY_NAME="autocv"

OCPUS=1            # bloco menor acha vaga mais fácil (free tier vai até 4/24)
MEM_GB=6
BOOT_GB=50
RETRY_SECONDS=60
# ----------------------------------------------------------------------------

fail() { echo "ERRO: $*" >&2; exit 1; }

META_FILE=""
cleanup() { [[ -n "$META_FILE" && -f "$META_FILE" ]] && rm -f "$META_FILE"; }
trap cleanup EXIT INT TERM

command -v oci     >/dev/null 2>&1 || fail "OCI CLI não encontrado. Rode no OCI Cloud Shell."
command -v python3 >/dev/null 2>&1 || fail "python3 não encontrado."

# --- compartment -----------------------------------------------------------
COMPARTMENT_ID="${COMPARTMENT_ID:-${OCI_TENANCY:-}}"
[[ "$COMPARTMENT_ID" == ocid1.* ]] \
  || fail "Compartment não encontrado. Preencha COMPARTMENT_ID no topo."

# --- chave SSH (gera se faltar) --------------------------------------------
if [[ ! -f "$SSH_PUBKEY_FILE" ]]; then
  echo "Gerando chave SSH em ${SSH_PUBKEY_FILE%.pub} ..."
  ssh-keygen -t rsa -b 4096 -f "${SSH_PUBKEY_FILE%.pub}" -N "" >/dev/null \
    || fail "Falha ao gerar a chave SSH."
fi

# --- subnet pública --------------------------------------------------------
if [[ -z "$SUBNET_ID" ]]; then
  echo "Detectando subnet pública..."
  SUBNET_ID="$(oci network subnet list -c "$COMPARTMENT_ID" \
    --query 'data[?"prohibit-public-ip-on-vnic"==`false`]|[0].id' \
    --raw-output 2>/dev/null)"
fi
[[ "$SUBNET_ID" == ocid1.subnet* ]] \
  || fail "Nenhuma subnet pública encontrada. Crie a VCN pelo VCN Wizard ('Create VCN with Internet Connectivity') ou preencha SUBNET_ID."

# --- Availability Domains --------------------------------------------------
# Sem --raw-output a saída é um array JSON; o python achata em 1 nome por linha.
# (Com --raw-output vinham os colchetes "[" e "]" junto e o request quebrava.)
if [[ ${#AD_LIST[@]} -eq 0 ]]; then
  echo "Resolvendo Availability Domains..."
  mapfile -t AD_LIST < <(oci iam availability-domain list --query 'data[].name' 2>/dev/null \
    | python3 -c "import json,sys;[print(x) for x in json.load(sys.stdin)]" 2>/dev/null)
fi

# Rede de segurança: descarta qualquer coisa que não pareça nome de AD.
VALID_ADS=()
for ad in "${AD_LIST[@]:-}"; do
  [[ "$ad" == *AD-* ]] && VALID_ADS+=("$ad")
done
AD_LIST=("${VALID_ADS[@]:-}")
[[ ${#AD_LIST[@]} -gt 0 && -n "${AD_LIST[0]}" ]] \
  || fail "Não consegui obter os nomes das ADs. Preencha AD_LIST manualmente (ex.: AD_LIST=(\"SzOZ:SA-SAOPAULO-1-AD-1\"))."

# --- imagem ----------------------------------------------------------------
if [[ -z "$IMAGE_ID" ]]; then
  echo "Resolvendo a imagem Ubuntu 22.04 (ARM) mais recente..."
  IMAGE_ID="$(oci compute image list --compartment-id "$COMPARTMENT_ID" \
    --operating-system "Canonical Ubuntu" --operating-system-version "22.04" \
    --shape "VM.Standard.A1.Flex" --sort-by TIMECREATED --sort-order DESC \
    --query 'data[0].id' --raw-output 2>/dev/null)"
fi
[[ "$IMAGE_ID" == ocid1.image* ]] || fail "Não consegui resolver a imagem. Preencha IMAGE_ID."

# --- metadata (chave SSH) via arquivo, pra não quebrar o JSON do request ----
META_FILE="$(mktemp /tmp/oci_meta_XXXXXX.json)"
python3 -c "import json,os,sys;open(sys.argv[1],'w').write(json.dumps({'ssh_authorized_keys':open(os.path.expanduser(sys.argv[2])).read().strip()}))" \
  "$META_FILE" "$SSH_PUBKEY_FILE" || fail "Falha ao montar o metadata."

SHAPE_CONFIG="{\"ocpus\":${OCPUS},\"memoryInGBs\":${MEM_GB}}"

echo "======================================================"
echo "Compartment : $COMPARTMENT_ID"
echo "Subnet      : $SUBNET_ID"
echo "ADs         : ${AD_LIST[*]}"
echo "Shape       : VM.Standard.A1.Flex (${OCPUS} OCPU / ${MEM_GB} GB, disco ${BOOT_GB} GB)"
echo "Imagem      : $IMAGE_ID"
echo "======================================================"

attempt=0
while true; do
  for AD in "${AD_LIST[@]}"; do
    attempt=$((attempt+1))
    echo "[$(date '+%H:%M:%S')] Tentativa #$attempt — AD=$AD ..."

    OUT="$(oci compute instance launch \
      --availability-domain "$AD" \
      --compartment-id "$COMPARTMENT_ID" \
      --shape "VM.Standard.A1.Flex" \
      --shape-config "$SHAPE_CONFIG" \
      --image-id "$IMAGE_ID" \
      --subnet-id "$SUBNET_ID" \
      --assign-public-ip true \
      --boot-volume-size-in-gbs "$BOOT_GB" \
      --display-name "$DISPLAY_NAME" \
      --metadata "file://${META_FILE}" \
      --wait-for-state RUNNING 2>&1)"
    RC=$?

    if [[ $RC -eq 0 ]]; then
      INST_ID="$(printf '%s' "$OUT" \
        | python3 -c "import json,sys
try:
    print(json.load(sys.stdin)['data']['id'])
except Exception:
    pass" 2>/dev/null)"
      [[ -n "$INST_ID" ]] || INST_ID="$(printf '%s' "$OUT" | grep -oE 'ocid1\.instance[a-z0-9._-]+' | head -n1)"

      PUB_IP="$(oci compute instance list-vnics --instance-id "$INST_ID" \
        --query 'data[0]."public-ip"' --raw-output 2>/dev/null)"

      echo "======================================================"
      echo "SUCESSO! Instância criada e em execução."
      echo "Instance OCID : $INST_ID"
      echo "IP público    : $PUB_IP"
      echo "======================================================"
      echo "Conecte com:  ssh ubuntu@$PUB_IP"
      exit 0
    fi

    if printf '%s' "$OUT" | grep -qi "capacity"; then
      echo "  -> sem capacidade agora. Nova tentativa em ${RETRY_SECONDS}s."
    else
      echo "  -> ERRO diferente de capacidade. Detalhes abaixo — parando pra você conferir:"
      echo "$OUT"
      exit 1
    fi
  done
  sleep "$RETRY_SECONDS"
done
