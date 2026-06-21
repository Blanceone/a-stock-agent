"""
deploy_cloud_remote.py — 远程部署 SearXNG + RSSHub 到阿里云服务器
通过 paramiko 使用密码认证执行。
"""
import paramiko
import textwrap

SERVER = "8.137.174.58"
USER = "root"
PASSWORD = "lxh107016!"
DEPLOY_DIR = "/opt/astock"

# docker-compose for cloud services
COMPOSE_YML = textwrap.dedent("""\
services:
  searxng:
    image: searxng/searxng:latest
    container_name: astock_searxng
    ports:
      - "8080:8080"
    volumes:
      - ./searxng:/etc/searxng
    environment:
      - SEARXNG_BASE_URL=http://localhost:8080/
    restart: unless-stopped

  rsshub:
    image: diygod/rsshub:latest
    container_name: astock_rsshub
    ports:
      - "1200:1200"
    environment:
      - NODE_ENV=production
      - CACHE_TYPE=memory
      - CACHE_EXPIRE=300
    restart: unless-stopped
""")

# SearXNG settings
SEARXNG_SETTINGS = textwrap.dedent("""\
use_default_settings: true

general:
  instance_name: "AStock SearXNG"

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "zh"

server:
  secret_key: "astock_searxng_secret_2024"
  limiter: false
  image_proxy: false
""")


def run_cmd(client, cmd, show=True, timeout=600):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if show and out:
        # 只输出最后10行
        lines = out.split('\n')
        for line in lines[-10:]:
            print(f"  > {line}")
    if err and "warning" not in err.lower():
        print(f"  ERR: {err}")
    return out


def main():
    print(f"{'='*50}")
    print(f" A股投研智能体 — 阿里云服务部署")
    print(f"{'='*50}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"[1] 连接 {SERVER}...")
    client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
    print("  连接成功")

    print("[2] 创建部署目录...")
    run_cmd(client, f"mkdir -p {DEPLOY_DIR}/searxng {DEPLOY_DIR}/rsshub")

    print("[3] 写入 docker-compose.yml...")
    run_cmd(client, f"cat > {DEPLOY_DIR}/docker-compose.yml << 'EOF'\n{COMPOSE_YML}EOF")

    print("[4] 写入 SearXNG settings.yml...")
    run_cmd(client, f"cat > {DEPLOY_DIR}/searxng/settings.yml << 'EOF'\n{SEARXNG_SETTINGS}EOF")

    print("[5] 拉取镜像并启动服务...")
    run_cmd(client, f"cd {DEPLOY_DIR} && docker compose pull", show=True)
    run_cmd(client, f"cd {DEPLOY_DIR} && docker compose up -d", show=True)

    print("[6] 验证服务...")
    run_cmd(client, "docker ps --format 'table {{.Names}}\\t{{.Status}}'")

    client.close()
    print(f"\n{'='*50}")
    print(f" 部署完成！")
    print(f" SearXNG: http://{SERVER}:8080")
    print(f" RSSHub:  http://{SERVER}:1200")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
