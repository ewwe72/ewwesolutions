#!/usr/bin/env bash
# Claude Code Playspace VM -- guest-side bootstrap.
# Target: Ubuntu Server 24.04 LTS, run as the non-root sudoer.
# Idempotent: re-running is safe.

set -euo pipefail

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
log() { printf '\n\033[1;36m[bootstrap]\033[0m %s\n' "$*"; }

# Ask once for sudo, then keep it warm for the whole run
sudo -v
( while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done ) 2>/dev/null &
SUDO_KEEPALIVE=$!
trap 'kill $SUDO_KEEPALIVE 2>/dev/null || true' EXIT

# --- 1. Base packages ----------------------------------------------------
log "apt update + base packages"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  curl wget git tmux jq ripgrep fzf build-essential \
  ca-certificates gnupg lsb-release unzip zip nano \
  python3 python3-pip python3-venv pipx \
  htop ncdu net-tools

# --- 2. Docker (official apt repo, not snap) -----------------------------
log "Docker CE + compose plugin"
sudo install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.asc ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo tee /etc/apt/keyrings/docker.asc > /dev/null
  sudo chmod a+r /etc/apt/keyrings/docker.asc
fi
ARCH=$(dpkg --print-architecture)
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $CODENAME stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -qq
sudo apt-get install -y -qq \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$REAL_USER"

# --- 3. Node.js LTS (NodeSource) -----------------------------------------
if ! command -v node >/dev/null 2>&1; then
  log "Node.js LTS via NodeSource"
  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi

# --- 4. GitHub CLI -------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  log "GitHub CLI"
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
  sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$ARCH signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq gh
fi

# --- 5. Claude Code CLI --------------------------------------------------
log "Claude Code CLI (global npm)"
sudo npm install -g @anthropic-ai/claude-code

# --- 6. Playwright + headless Chromium (for Claude's browser access) -----
log "Playwright + Chromium (as $REAL_USER)"
sudo -u "$REAL_USER" -H bash <<'EOSU'
set -euo pipefail
mkdir -p "$HOME/playwright-claude"
cd "$HOME/playwright-claude"
[ -f package.json ] || npm init -y >/dev/null
npm install --silent playwright
EOSU
# Chromium system deps need root; the browser binary itself lives in user's cache
sudo -u "$REAL_USER" -H bash -c 'cd ~/playwright-claude && npx playwright install chromium'
sudo -u "$REAL_USER" -H bash -c 'cd ~/playwright-claude && npx playwright install-deps chromium' || \
  sudo bash -c "cd $REAL_HOME/playwright-claude && npx playwright install-deps chromium"

# --- 7. Project root + shell defaults ------------------------------------
log "Workspace dir + shell aliases"
sudo -u "$REAL_USER" -H bash <<'EOSU'
set -euo pipefail
mkdir -p "$HOME/playspace"
if ! grep -q 'Claude Code Playspace' "$HOME/.bashrc" 2>/dev/null; then
  cat >> "$HOME/.bashrc" <<'EOF'

# --- Claude Code Playspace ---
export EDITOR=nano
alias ll='ls -alh'
alias gs='git status'
alias dc='docker compose'
cd ~/playspace 2>/dev/null || true
EOF
fi
EOSU

# --- 8. Tailscale (optional, comment out if you don't want it) -----------
if ! command -v tailscale >/dev/null 2>&1; then
  log "Tailscale (you'll run 'sudo tailscale up' manually after install)"
  curl -fsSL https://tailscale.com/install.sh | sudo sh
fi

# --- Done ----------------------------------------------------------------
log "Bootstrap complete."
echo
echo "Versions installed:"
printf '  node:    %s\n' "$(node --version 2>/dev/null || echo MISSING)"
printf '  npm:     %s\n' "$(npm --version 2>/dev/null || echo MISSING)"
printf '  docker:  %s\n' "$(docker --version 2>/dev/null || echo MISSING)"
printf '  gh:      %s\n' "$(gh --version 2>/dev/null | head -n1 || echo MISSING)"
printf '  claude:  %s\n' "$(claude --version 2>/dev/null || echo MISSING)"
echo
echo "Next manual steps:"
echo "  1. exit + ssh back in (so docker group membership takes effect)"
echo "  2. claude login                    # paste your *scoped* Anthropic API key"
echo "  3. gh auth login                   # paste your *scoped* GitHub PAT"
echo "  4. git clone <playspace repo> ~/playspace/random"
echo "  5. sudo tailscale up               # only if you want phone/LAN access"
