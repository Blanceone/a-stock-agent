#!/bin/bash
# deploy_cloud.sh — 阿里云服务器部署 SearXNG + RSSHub
# 服务器: 8.137.174.58 (root)
# 用法: bash scripts/deploy_cloud.sh
#
# 前置条件:
#   1. 已配置 SSH 免密登录到 root@8.137.174.58
#   2. 服务器已安装 Docker + Docker Compose

set -euo pipefail

SERVER="root@8.137.174.58"
DEPLOY_DIR="/opt/astock"

echo "========================================="
echo " A股投研智能体 — 阿里云服务部署"
echo "========================================="

# 1. 在服务器上创建部署目录
echo "[1/5] 创建服务器部署目录..."
ssh "$SERVER" "mkdir -p $DEPLOY_DIR/searxng $DEPLOY_DIR/rsshub"

# 2. 上传 docker-compose.cloud.yml
echo "[2/5] 上传 docker-compose.cloud.yml..."
scp docker-compose.cloud.yml "$SERVER:$DEPLOY_DIR/docker-compose.yml"

# 3. 创建 SearXNG 配置
echo "[3/5] 配置 SearXNG..."
ssh "$SERVER" "cat > $DEPLOY_DIR/searxng/settings.yml << 'SETTINGS_EOF'
use_default_settings: true

general:
  instance_name: \"AStock SearXNG\"

search:
  safe_search: 0
  autocomplete: \"\"
  default_lang: \"zh\"

server:
  secret_key: \"astock_searxng_secret_2024\"
  limiter: false
  image_proxy: false

engines:
  - name: bing
    engine: bing
    shortcut: bi
    disabled: false
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
    disabled: false
  - name: baidu
    engine: xpath
    shortcut: bd
    disabled: false
SETTINGS_EOF"

# 4. 启动服务
echo "[4/5] 启动 Docker 服务..."
ssh "$SERVER" "cd $DEPLOY_DIR && docker compose up -d"

# 5. 验证
echo "[5/5] 验证服务..."
sleep 5

echo ""
echo "--- SearXNG ---"
curl -s -o /dev/null -w "HTTP %{http_code}" "http://8.137.174.58:8080/search?q=test&format=json" || echo "  (可能需要等待启动)"
echo ""

echo "--- RSSHub ---"
curl -s -o /dev/null -w "HTTP %{http_code}" "http://8.137.174.58:1200/" || echo "  (可能需要等待启动)"
echo ""

echo ""
echo "========================================="
echo " 部署完成！"
echo " SearXNG: http://8.137.174.58:8080"
echo " RSSHub:  http://8.137.174.58:1200"
echo "========================================="
