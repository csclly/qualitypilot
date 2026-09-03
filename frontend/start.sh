#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .tools/node/bin/node ]; then
  export PATH="$PWD/.tools/node/bin:$PATH"
fi
if ! command -v node >/dev/null 2>&1; then
  echo "请先安装 Node.js 22.12+ 或 24 LTS，再重新运行此脚本。"
  exit 1
fi
if [ ! -d node_modules ]; then npm ci; fi
exec npm run dev
