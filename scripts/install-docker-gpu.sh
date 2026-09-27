#!/usr/bin/env bash
# VoiceStudio: one-shot LAN install with Docker + NVIDIA GPU.
#
#   curl -fsSL https://raw.githubusercontent.com/Clawdio900/VoiceStudio/webapp-server/scripts/install-docker-gpu.sh | sudo bash
#   (or run it from a checkout: sudo bash scripts/install-docker-gpu.sh)
#
# What it does (safe to re-run; it updates in place and keeps your data/key):
#   1. checks the NVIDIA driver (nvidia-smi)
#   2. installs Docker Engine + Compose plugin if missing
#   3. installs the NVIDIA Container Toolkit if missing and wires it into Docker
#   4. clones or updates VoiceStudio into $INSTALL_DIR
#   5. writes deploy/.env (LAN IP, port, API key) unless it already exists
#   6. builds and starts VoiceStudio on https://<LAN IP>:$PORT
#
# Environment overrides:
#   INSTALL_DIR=/opt/voicestudio  PORT=9999  LAN_IP=<auto>  HF_TOKEN=<none>  FORCE_CPU=0
#   LOW_MEMORY=0 (forced on automatically below 8 GB RAM)
#   REPO_URL=https://github.com/Clawdio900/VoiceStudio.git  BRANCH=webapp-server
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/voicestudio}"
PORT="${PORT:-9999}"
REPO_URL="${REPO_URL:-https://github.com/Clawdio900/VoiceStudio.git}"
BRANCH="${BRANCH:-webapp-server}"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "This script supports Linux servers only."
[ "$(uname -m)" = "x86_64" ] || die "The VoiceStudio image is x86-64 only (found $(uname -m))."
[ "$(id -u)" -eq 0 ] || die "Run as root: sudo bash $0"

# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-} ${ID_LIKE:-}" in
  *debian*|*ubuntu*) PKG=apt ;;
  *rhel*|*fedora*|*centos*|*rocky*|*almalinux*) PKG=dnf ;;
  *) die "Unsupported distribution '${ID:-unknown}'. Use Ubuntu/Debian or a RHEL/Fedora family distro." ;;
esac

# ── 1. NVIDIA driver ───────────────────────────────────────────────────────
say "Checking NVIDIA driver"
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
  warn "nvidia-smi is missing or cannot talk to the GPU."
  if [ "$PKG" = apt ] && command -v ubuntu-drivers >/dev/null 2>&1; then
    warn "Install the driver with:  sudo ubuntu-drivers install && sudo reboot"
  else
    warn "Install the NVIDIA proprietary driver for your distro, reboot, then re-run this script."
  fi
  die "NVIDIA driver not ready."
fi
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | sed 's/^/    GPU: /'
# The bundled PyTorch build only ships kernels for compute capability >= 7.0
# (Volta and newer). Older GPUs are detected by CUDA but fail at kernel launch,
# so run on CPU for them instead of handing the container a GPU it cannot use.
USE_GPU=1
CAP="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
if [ -n "$CAP" ] && awk -v c="$CAP" 'BEGIN{exit !(c+0 < 7.0)}'; then
  warn "GPU compute capability $CAP is below 7.0 and unsupported by the bundled PyTorch; VoiceStudio will run on CPU."
  USE_GPU=0
fi
[ "${FORCE_CPU:-0}" = 1 ] && USE_GPU=0

# ── 2. Docker Engine + Compose ─────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker Engine"
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || {
  say "Installing Docker Compose plugin"
  if [ "$PKG" = apt ]; then apt-get update -qq && apt-get install -y -qq docker-compose-plugin
  else dnf install -y -q docker-compose-plugin; fi
}
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is not available."

# ── 3. NVIDIA Container Toolkit ────────────────────────────────────────────
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  say "Installing NVIDIA Container Toolkit"
  if [ "$PKG" = apt ]; then
    apt-get update -qq && apt-get install -y -qq curl gnupg ca-certificates
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -qq && apt-get install -y -qq nvidia-container-toolkit
  else
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
      > /etc/yum.repos.d/nvidia-container-toolkit.repo
    dnf install -y -q nvidia-container-toolkit
  fi
fi
if ! grep -q '"nvidia"' /etc/docker/daemon.json 2>/dev/null; then
  say "Registering the NVIDIA runtime with Docker"
  nvidia-ctk runtime configure --runtime=docker >/dev/null
  systemctl restart docker
fi
[ "$USE_GPU" = 1 ] && say "Testing GPU access from a container"
if [ "$USE_GPU" = 1 ]; then
  docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L >/dev/null 2>&1 \
    || die "Docker cannot reach the GPU. Check 'nvidia-ctk runtime configure --runtime=docker' and restart Docker."
fi

# ── 4. Source ──────────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || {
  if [ "$PKG" = apt ]; then apt-get install -y -qq git; else dnf install -y -q git; fi
}
if [ -d "$INSTALL_DIR/.git" ]; then
  say "Updating $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout -q -B "$BRANCH" FETCH_HEAD
else
  say "Cloning VoiceStudio into $INSTALL_DIR"
  git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# ── 5. Configuration ───────────────────────────────────────────────────────
ENV_FILE=deploy/.env
if [ ! -f "$ENV_FILE" ]; then
  # Prefer the address of the default-route interface; skip VPN/Docker
  # interfaces (tun*, wg*, tailscale*, docker*, br-*, veth*).
  if [ -z "${LAN_IP:-}" ]; then
    dev="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')"
    case "$dev" in tun*|wg*|tailscale*|docker*|br-*|veth*|"") dev="" ;; esac
    [ -n "$dev" ] && LAN_IP="$(ip -4 -o addr show dev "$dev" scope global | awk '{split($4,a,"/"); print a[1]; exit}')"
  fi
  [ -n "${LAN_IP:-}" ] || LAN_IP="$(ip -4 -o addr show scope global | awk '$2 !~ /^(tun|wg|tailscale|docker|br-|veth)/ {split($4,a,"/"); print a[1]; exit}')"
  echo "    Detected LAN IP: $LAN_IP (override with LAN_IP=... if wrong)"
  [ -n "${LAN_IP:-}" ] || die "Could not detect the LAN IP. Re-run with LAN_IP=192.168.x.y"
  say "Writing $ENV_FILE (LAN IP $LAN_IP, port $PORT)"
  umask 077
  {
    echo "VOICESTUDIO_HOST=$LAN_IP"
    echo "VOICESTUDIO_PORT=$PORT"
    echo "OMNIVOICE_API_KEY=$(openssl rand -base64 32 | tr -d '\n')"
    echo "HF_TOKEN=${HF_TOKEN:-}"
  } > "$ENV_FILE"
else
  say "Keeping existing $ENV_FILE"
fi
# Servers under 8 GB RAM: enable the low-memory preset (added once, editable).
MEM_GB="$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)"
if { [ "$MEM_GB" -lt 8 ] || [ "${LOW_MEMORY:-0}" = 1 ]; } && ! grep -q '^# low-memory preset' "$ENV_FILE"; then
  say "Only ${MEM_GB} GB RAM: enabling the low-memory preset in $ENV_FILE"
  cat >> "$ENV_FILE" <<'LOWMEM'
# low-memory preset (servers with < 8 GB RAM); delete this block to disable
OMNIVOICE_PRELOAD_TTS=0
OMNIVOICE_PRELOAD_CAPTURE_ASR=0
OMNIVOICE_PRELOAD_WATERMARK=0
OMNIVOICE_IDLE_TIMEOUT_S=300
OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S=120
OMNIVOICE_UNLOAD_NLLB=1
OMNIVOICE_CPU_POOL=2
MALLOC_ARENA_MAX=2
OMNIVOICE_RAM_PREFLIGHT=0
LOWMEM
fi
# Older installs got the preset before the RAM-check opt-out existed.
if grep -q '^# low-memory preset' "$ENV_FILE" && ! grep -q '^OMNIVOICE_RAM_PREFLIGHT=' "$ENV_FILE"; then
  echo "OMNIVOICE_RAM_PREFLIGHT=0" >> "$ENV_FILE"
fi
# shellcheck disable=SC1090
. "./$ENV_FILE"

# ── 6. Build and start ─────────────────────────────────────────────────────
COMPOSE=(docker compose -f deploy/docker-compose.lan.yml)
[ "$USE_GPU" = 1 ] && COMPOSE+=(-f deploy/docker-compose.server.gpu.yml)
say "Building and starting VoiceStudio (first build takes 10-30 minutes)"
"${COMPOSE[@]}" up -d --build

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  say "Opening port $VOICESTUDIO_PORT in ufw for private networks"
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    ufw allow from "$net" to any port "$VOICESTUDIO_PORT" proto tcp >/dev/null
  done
fi

URL="https://$VOICESTUDIO_HOST:$VOICESTUDIO_PORT"
STATUS="is still starting (model downloads can take a while; check the logs)"
say "Waiting for VoiceStudio to become healthy (first start downloads models)"
for _ in $(seq 1 120); do
  if curl -ksf "$URL/health" >/dev/null 2>&1; then
    STATUS="is running"; break
  fi
  sleep 10
done

cat <<EOF

────────────────────────────────────────────────────────────────
 VoiceStudio $STATUS

   Open:     $URL
   API key:  $OMNIVOICE_API_KEY
             (also in $INSTALL_DIR/$ENV_FILE)

 Your browser will warn about the certificate once per device;
 choose "Advanced -> Proceed". To trust it permanently, install:
   cd $INSTALL_DIR && ${COMPOSE[*]} cp caddy:/data/caddy/pki/authorities/local/root.crt ./voicestudio-ca.crt

 Logs:     cd $INSTALL_DIR && ${COMPOSE[*]} logs -f voicestudio
 Update:   re-run this script
 Stop:     cd $INSTALL_DIR && ${COMPOSE[*]} down
────────────────────────────────────────────────────────────────
EOF
