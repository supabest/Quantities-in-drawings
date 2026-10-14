#!/usr/bin/env bash
# 一键部署：轻量应用服务器 (VPS) 上拉起整套装修工程量系统
# 适用：腾讯云轻量 / 阿里云轻量 / 华为云云耀 / 任何 Ubuntu 22.04+ VPS
# 用法：
#   1. SSH 进 VPS
#   2. curl -fsSL https://raw.githubusercontent.com/.../deploy/vps_deploy.sh | bash
#      或：git clone 本仓库后 bash deploy/vps_deploy.sh
#
# 前置条件：干净的 Ubuntu 22.04/24.04（22.04 亲测，24.04 理论兼容）
# 脚本幂等：重复执行安全，已安装的组件会跳过

set -euo pipefail

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERR]${NC} $*" >&2; }

# ── 0. 检查 root ──
if [[ $EUID -ne 0 ]]; then
  err "请用 root 执行：sudo bash vps_deploy.sh"
  exit 1
fi

# ── 1. 环境变量（可提前 export 覆盖） ──
APP_DIR="${APP_DIR:-/opt/decoration-quantity}"
REPO_URL="${REPO_URL:-https://github.com/alickchen222-web/decoration-quantity.git}"
BRANCH="${BRANCH:-main}"

# 首次 admin 密码：不设置则随机生成一次性密码
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@decoration-quant.com}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

# Flask secret key：不设置则随机生成
SECRET_KEY="${SECRET_KEY:-}"

# PostgreSQL 密码
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-quant_prod_$(openssl rand -hex 8)}"
POSTGRES_USER="${POSTGRES_USER:-quant}"
POSTGRES_DB="${POSTGRES_DB:-decoration_quant}"

info "===== 装修工程量系统 · VPS 一键部署 ====="
info "安装目录：$APP_DIR"
info "仓库：$REPO_URL (分支 $BRANCH)"
echo

# ── 2. 系统更新 + 基础工具 ──
info "安装基础工具..."
apt-get update -qq
apt-get install -y -qq curl git wget ufw > /dev/null

# ── 3. 安装 Docker（如未安装） ──
if command -v docker &> /dev/null; then
  info "Docker 已安装：$(docker --version)"
else
  info "安装 Docker..."
  # 使用官方脚本，国内 VPS 可能需要代理；备选：阿里云镜像
  curl -fsSL https://get.docker.com | bash
  systemctl enable --now docker
  info "Docker 安装完成：$(docker --version)"
fi

# 安装 Docker Compose 插件（如缺失）
if docker compose version &> /dev/null; then
  info "Docker Compose 已就绪"
else
  info "安装 Docker Compose 插件..."
  apt-get install -y -qq docker-compose-plugin > /dev/null
fi

# ── 4. 克隆/更新仓库 ──
if [[ -d "$APP_DIR/.git" ]]; then
  info "仓库已存在，拉取最新..."
  cd "$APP_DIR"
  git fetch origin
  git checkout "$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  info "克隆仓库..."
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  cd "$APP_DIR"
fi

# ── 5. 生成 .env ──
if [[ -z "$SECRET_KEY" ]]; then
  SECRET_KEY=$(openssl rand -hex 32)
  info "生成 SECRET_KEY"
fi

if [[ -z "$ADMIN_PASSWORD" ]]; then
  ADMIN_PASSWORD=$(openssl rand -hex 8)
  warn "生成一次性 ADMIN_PASSWORD：$ADMIN_PASSWORD"
  warn "请登录后立即修改！"
fi

info "写入 .env..."
cat > "$APP_DIR/.env" <<EOF
# VPS 部署环境变量 · 生成于 $(date -Iseconds)
SECRET_KEY=$SECRET_KEY
ADMIN_EMAIL=$ADMIN_EMAIL
ADMIN_PASSWORD=$ADMIN_PASSWORD

POSTGRES_USER=$POSTGRES_USER
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=$POSTGRES_DB

# 宿主机映射端口（避免与本地 PG 冲突）
PG_HOST_PORT=5433
EOF

chmod 600 "$APP_DIR/.env"

# ── 6. 启动服务 ──
info "构建并启动容器..."
cd "$APP_DIR"
docker compose pull db 2>/dev/null || true
docker compose up -d --build

# ── 7. 等待健康检查 ──
info "等待 PostgreSQL 就绪..."
for i in {1..30}; do
  if docker compose exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" &>/dev/null; then
    info "PostgreSQL 就绪"
    break
  fi
  if [[ $i -eq 30 ]]; then
    err "PostgreSQL 启动超时，检查日志：docker compose logs db"
    exit 1
  fi
  sleep 2
done

# ── 8. 配置防火墙 ──
info "配置防火墙..."
ufw allow OpenSSH
ufw allow 5000/tcp
ufw --force enable > /dev/null 2>&1 || true

# ── 9. 获取公网 IP ──
PUBLIC_IP=$(curl -s https://ifconfig.me || curl -s https://api.ipify.org || echo "YOUR_VPS_IP")

# ── 10. 完成 ──
echo
echo "=========================================="
info "部署完成！"
echo "=========================================="
echo
echo "访问地址：http://$PUBLIC_IP:5000"
echo "管理员邮箱：$ADMIN_EMAIL"
echo "管理员密码：$ADMIN_PASSWORD"
echo
warn "请立即登录并修改密码！"
warn "如需 HTTPS，建议配 Cloudflare Tunnel（见 docs/cloudflare_tunnel.md）"
echo
echo "常用命令："
echo "  查看日志：cd $APP_DIR && docker compose logs -f"
echo "  重启服务：cd $APP_DIR && docker compose restart"
echo "  更新代码：cd $APP_DIR && git pull && docker compose up -d --build"
echo "  备份数据库：docker compose exec db pg_dump -U $POSTGRES_USER $POSTGRES_DB > backup.sql"
echo
