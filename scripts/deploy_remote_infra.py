"""deploy_remote_infra.py — 在远程服务器上启动 PG/Redis/ChromaDB"""
import paramiko
import time

REMOTE_HOST = "8.137.174.58"
SSH_USER = "root"
SSH_PASS = "lxh107016!"

COMPOSE_CONTENT = r'''services:
  searxng:
    image: searxng/searxng:latest
    container_name: astock_searxng
    ports:
      - "8888:8080"
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

  postgres:
    image: postgres:16-alpine
    container_name: astock_postgres
    environment:
      POSTGRES_USER: astock
      POSTGRES_PASSWORD: astock
      POSTGRES_DB: astock
    ports:
      - "5432:5432"
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: astock_redis
    ports:
      - "6379:6379"
    command: redis-server --save 60 1 --loglevel warning
    volumes:
      - ./data/redis:/data
    restart: unless-stopped

  chromadb:
    image: chromadb/chroma:0.5.15
    container_name: astock_chromadb
    ports:
      - "8000:8000"
    volumes:
      - ./data/chromadb:/chroma/chroma
    environment:
      IS_PERSISTENT: "TRUE"
    restart: unless-stopped
'''


def run_cmd(ssh, cmd, show=True):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if show:
        if out.strip():
            print(f"  [OUT] {out.strip()[:200]}")
        if err.strip():
            print(f"  [ERR] {err.strip()[:200]}")
    return out, err


def main():
    print(f"[Deploy] 连接 {REMOTE_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(REMOTE_HOST, username=SSH_USER, password=SSH_PASS)
    print("[Deploy] 已连接")

    # Write docker-compose.yml
    sftp = ssh.open_sftp()
    with sftp.open("/opt/astock/docker-compose.yml", "w") as f:
        f.write(COMPOSE_CONTENT)
    sftp.close()
    print("[Deploy] docker-compose.yml 已更新")

    # Create data dirs
    run_cmd(ssh, "mkdir -p /opt/astock/data/postgres /opt/astock/data/redis /opt/astock/data/chromadb")

    # Pull and start services
    print("[Deploy] 拉取镜像并启动...")
    run_cmd(ssh, "cd /opt/astock && docker compose pull postgres redis chromadb", show=True)
    run_cmd(ssh, "cd /opt/astock && docker compose up -d", show=True)

    # Wait for services to start
    print("[Deploy] 等待服务启动 (15s)...")
    time.sleep(15)

    # Check status
    print("[Deploy] 检查容器状态:")
    run_cmd(ssh, "docker ps --format 'table {{.Names}}\t{{.Status}}'")

    # Verify PG
    print("[Deploy] 验证 PostgreSQL:")
    out, _ = run_cmd(ssh, "docker exec astock_postgres psql -U astock -c 'SELECT 1'", show=False)
    print(f"  PG: {'OK' if '1 row' in out else 'FAIL'}")

    # Verify Redis
    print("[Deploy] 验证 Redis:")
    out, _ = run_cmd(ssh, "docker exec astock_redis redis-cli ping", show=False)
    print(f"  Redis: {out.strip()}")

    # Check if stock_basic table exists (from previous sessions)
    print("[Deploy] 检查 stock_basic 表:")
    out, _ = run_cmd(ssh, "docker exec astock_postgres psql -U astock -d astock -c \"SELECT COUNT(*) FROM stock_basic\" 2>&1", show=False)
    print(f"  stock_basic: {out.strip()}")

    ssh.close()
    print("[Deploy] 完成")


if __name__ == "__main__":
    main()
