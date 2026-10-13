#!/usr/bin/env bash
# deploy/deploy_pages.sh — 第 1 档：重算 → 构建静态站 → 发布到 Cloudflare Pages
#
# 为什么"本地跑一次"而不是"云端 CI 构建"：几何链要读 装修图纸/（体积大、刻意不入库），
# 云端拿不到图纸就算不出数。所以链在本地跑，产物 site/public/ 入库，Pages 只发静态文件。
#
# 用法:
#   ./deploy/deploy_pages.sh              # 全量：重算 xlsx + 建站 + 发布
#   ./deploy/deploy_pages.sh --no-rebuild # 只发布已构建好的 site/public
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-venv/bin/python}
PROJECT="${PAGES_PROJECT:-decoration-quantity}"

if [ "${1:-}" != "--no-rebuild" ]; then
  echo "[1/3] 重算工程量计算表(几何链 + 恒等断言)…"
  "$PY" scripts/build_decoration_workbook.py
  echo "[2/3] 构建静态站…"
  "$PY" scripts/build_static_site.py
else
  echo "[1/3][2/3] 跳过重算/构建(--no-rebuild)"
fi

echo "[3/3] 发布到 Cloudflare Pages (project=$PROJECT)…"
npx --yes wrangler pages deploy site/public --project-name="$PROJECT" --branch=main

echo
echo "线上地址: https://$PROJECT.pages.dev/"
echo "自检:"
for p in / /boq /pending /notes /robots.txt; do
  printf "  %-12s %s\n" "$p" "$(curl -sL -o /dev/null -w '%{http_code}' "https://$PROJECT.pages.dev$p")"
done
echo "  下载: https://$PROJECT.pages.dev/download/227%E6%88%B7%E5%9E%8B%E8%A3%85%E4%BF%AE%E5%B7%A5%E7%A8%8B%E9%87%8F%E8%AE%A1%E7%AE%97%E8%A1%A8.xlsx"
