#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 2 ]]; then
  printf '用法：bash proxy.sh <本机代理端口> <SSH主机别名...>\n'
  printf '批量：bash proxy.sh <本机代理端口> --host-file hosts.txt\n'
  exit 2
fi
proxy_port=$1
shift
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/edge-deploy.py" up --proxy-port "$proxy_port" --proxy-only "$@"
