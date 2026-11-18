#!/usr/bin/env bash
# Run on the computer that can SSH into the Ubuntu device.
set -euo pipefail

usage() {
  cat <<'HELP'
Usage:
  bash edge-codex.sh install SSH_HOST [--mac-proxy] [--bundle FILE --sha256 HEX]
  bash edge-codex.sh login   SSH_HOST [--mac-proxy | --edge-proxy]
  bash edge-codex.sh run     SSH_HOST [--mac-proxy | --edge-proxy]

SSH_HOST is a host alias or user@host in your OpenSSH configuration.
--mac-proxy uses the existing ~/.codex-mac-proxy/with-mac-proxy on the device.
--bundle installs a trusted .tar.gz with a top-level executable named codex
(and any companion files), built for that device. Pass its published SHA-256.
HELP
}

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
if [[ $# -lt 2 ]]; then usage >&2; exit 2; fi
action=$1
target=$2
shift 2
case "$action" in install|login|run) ;; *) usage >&2; exit 2 ;; esac
if [[ ! "$target" =~ ^[a-zA-Z0-9_][a-zA-Z0-9._@-]*$ ]]; then
  echo 'Use an SSH host alias or user@host.' >&2
  exit 2
fi

proxy=direct
bundle=
checksum=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mac-proxy) proxy=mac; shift ;;
    --edge-proxy) proxy=edge; shift ;;
    --bundle) bundle="${2:?Missing bundle path}"; shift 2 ;;
    --sha256) checksum="${2:?Missing SHA-256}"; shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
if [[ -n "$bundle" || -n "$checksum" ]]; then
  if [[ "$action" != install || ! -f "$bundle" || ! "$checksum" =~ ^[a-fA-F0-9]{64}$ ]]; then
    echo 'Offline install needs a readable --bundle file and a 64-digit --sha256.' >&2
    exit 2
  fi
else
  checksum=none
fi

IFS= read -r -d '' remote_program <<'REMOTE' || true
set -euo pipefail
action=$1
proxy=$2
checksum=$3
bin="$HOME/.local/bin"
cache="$HOME/.local/share/edge-codex"
helper="$HOME/.codex-mac-proxy/with-mac-proxy"
if [[ "$proxy" == edge ]]; then helper="$HOME/.local/share/edge-deploy/with-proxy"; fi

fail() { printf '%s\n' "$*" >&2; exit 1; }
routed() {
  if [[ "$proxy" == direct ]]; then "$@"; else "$helper" "$@"; fi
}
find_codex() {
  if [[ -x "$bin/codex" ]] && timeout 10 "$bin/codex" --version >&2; then
    printf '%s\n' "$bin/codex"
    return
  fi
  local candidate
  candidate=$(command -v codex) || return 1
  [[ "$candidate" != "$bin/codex" ]] || return 1
  timeout 10 "$candidate" --version >&2 || return 1
  printf '%s\n' "$candidate"
}
report() {
  printf 'INSTALLED %s\n' "$1"
  if [[ "$checksum" != none ]]; then
    printf 'NETWORK skipped (offline bundle mode)\n'
  elif command -v curl >/dev/null && { [[ "$proxy" == direct ]] || [[ -x "$helper" ]]; }; then
    local host response
    for host in auth.openai.com chatgpt.com; do
      if response=$(routed curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 "https://$host/"); then
        printf 'NETWORK %s HTTP %s\n' "$host" "$response"
      else
        printf 'NETWORK %s unreachable\n' "$host"
      fi
    done
  else
    printf 'NETWORK skipped (curl or proxy helper unavailable)\n'
  fi
  if timeout 10 "$1" login status >/dev/null 2>&1; then
    printf 'LOGIN credentials present\n'
  else
    printf 'LOGIN not confirmed; use the login subcommand\n'
  fi
}

[[ "$(uname -s)" == Linux ]] || fail 'Target must be Linux.'
case "$(uname -m)" in x86_64|aarch64) ;; *) fail 'This prototype supports x86_64 and aarch64 targets.' ;; esac
command -v timeout >/dev/null || fail 'Target needs GNU timeout.'
[[ "$(id -u)" != 0 ]] || fail 'Connect as the user who will run Codex.'
if [[ "$action" == login || "$action" == run ]]; then
  codex_path=$(find_codex) || fail 'Install Codex first.'
  [[ "$proxy" == direct || -x "$helper" ]] || fail "Proxy helper missing: $helper"
  if [[ "$action" == login ]]; then routed "$codex_path" login --device-auth; else routed "$codex_path"; fi
  exit
fi

if codex_path=$(find_codex); then
  report "$codex_path"
  exit
fi
[[ ! -e "$bin/codex" && ! -L "$bin/codex" ]] || fail "Existing $bin/codex failed its version check. Inspect it before reinstalling."
mkdir -p "$bin" "$cache"
# Serialize this tool's installations for the same user.
command -v flock >/dev/null || fail 'Target needs flock (util-linux).'
exec 9>"$cache/install.lock"
flock -n 9 || fail 'A Codex installation is already running for this user.'
if codex_path=$(find_codex); then
  report "$codex_path"
  exit
fi
[[ ! -e "$bin/codex" && ! -L "$bin/codex" ]] || fail "An existing $bin/codex needs inspection."

tmp=$(mktemp -d "$cache/.install.XXXXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
if [[ "$checksum" != none ]]; then
  cat > "$tmp/bundle.tar.gz"
  printf '%s  %s\n' "$checksum" "$tmp/bundle.tar.gz" | sha256sum --check --status ||
    fail 'Bundle SHA-256 mismatch.'
  payload=$(mktemp -d "$cache/bundle.XXXXXXXX")
  if tar -xzf "$tmp/bundle.tar.gz" -C "$payload" &&
     [[ -f "$payload/codex" && -x "$payload/codex" && ! -L "$payload/codex" ]] &&
     timeout 10 "$payload/codex" --version; then
    if ! ln -s -- "$payload/codex" "$bin/codex"; then
      rm -rf -- "$payload"
      fail 'Could not activate the offline installation.'
    fi
  else
    rm -rf -- "$payload"
    fail 'Bundle must contain a runnable top-level codex for this Linux target.'
  fi
else
  [[ "$proxy" == direct || -x "$helper" ]] || fail "Proxy helper missing: $helper"
  if ! command -v curl >/dev/null && ! command -v wget >/dev/null && command -v apt-get >/dev/null && sudo -n true 2>/dev/null; then
    printf 'TRY installing curl with passwordless sudo through the configured proxy\n'
    routed timeout -k 5 180 sh -c '
      sudo -n apt-get -o Acquire::http::Proxy="${http_proxy:-DIRECT}" -o Acquire::https::Proxy="${https_proxy:-DIRECT}" update &&
      sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=30 -o Acquire::http::Proxy="${http_proxy:-DIRECT}" -o Acquire::https::Proxy="${https_proxy:-DIRECT}" install -y curl
    ' || printf 'Automatic curl installation failed; see the package manager output above.\n' >&2
  fi
  if command -v curl >/dev/null || command -v wget >/dev/null; then
    printf 'TRY official standalone installer\n'
    mkdir -p "$tmp/standalone"
    if { if command -v curl >/dev/null; then
      routed curl -fsSL --retry 1 --connect-timeout 8 --max-time 30 https://chatgpt.com/codex/install.sh -o "$tmp/install.sh"
    else
      routed wget -q -t 2 -T 30 -O "$tmp/install.sh" https://chatgpt.com/codex/install.sh
    fi; } &&
      routed timeout -k 5 120 env CODEX_NON_INTERACTIVE=1 \
        CODEX_INSTALL_DIR="$tmp/standalone" sh "$tmp/install.sh" &&
      timeout 10 "$tmp/standalone/codex" --version; then
      # The standalone launcher can reference other files. Keep it in place.
      ln -s -- "$tmp/standalone/codex" "$bin/codex"
      rm -f -- "$tmp/install.sh"
      trap - EXIT
    else
      printf 'FALLBACK official standalone installation failed\n' >&2
    fi
  fi
  if [[ ! -x "$bin/codex" ]]; then
    command -v node >/dev/null && command -v npm >/dev/null ||
      fail 'No usable standalone install and Node/npm unavailable. If both curl and wget are missing, install either with an administrator, then retry; proxy forwarding remains active. An offline --bundle also works.'
    printf 'TRY npm with a user-owned prefix and engine checks\n'
    npm_prefix=$(mktemp -d "$cache/npm.XXXXXXXX")
    if routed timeout -k 5 150 npm install --global --prefix "$npm_prefix" \
      --registry=https://registry.npmjs.org --engine-strict --no-audit --no-fund \
      --fetch-retries=1 --fetch-timeout=25000 @openai/codex &&
      timeout 10 "$npm_prefix/bin/codex" --version; then
      if ! ln -s -- "$npm_prefix/bin/codex" "$bin/codex"; then
        rm -rf -- "$npm_prefix"
        fail 'Could not activate the npm installation.'
      fi
    else
      rm -rf -- "$npm_prefix"
      fail 'npm fallback failed. See its error above or install from --bundle.'
    fi
  fi
fi

codex_path=$(find_codex) || fail 'Installed files did not pass the version check.'
report "$codex_path"
printf 'Use %s or add %s to PATH.\n' "$codex_path" "$bin"
REMOTE
# OpenSSH joins remote arguments before invoking the login shell. Escape the
# script as one POSIX shell argument. The other words are validated above.
quoted_program=$(printf '%s' "$remote_program" | sed "s/'/'\\\\''/g")
remote_command="bash -c '$quoted_program' -- $action $proxy $checksum"

if [[ "$action" == login || "$action" == run ]]; then
  ssh -t -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -- "$target" "$remote_command"
else
  ssh -T -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -- "$target" "$remote_command" < "${bundle:-/dev/null}"
fi
