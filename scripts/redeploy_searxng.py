"""重新部署 SearXNG 到 8888 端口"""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("8.137.174.58", username="root", password="lxh107016!", timeout=15)

# 更新 docker-compose.yml 使用 8888 端口
compose = """services:
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
"""

cmds = [
    ("清理旧容器", "docker stop astock_searxng 2>/dev/null; docker rm astock_searxng 2>/dev/null; echo OK"),
    ("更新compose", f"cat > /opt/astock/docker-compose.yml << 'EOF'\n{compose}EOF"),
    ("重启所有服务", "cd /opt/astock && docker compose up -d 2>&1"),
    ("等待启动", "sleep 10"),
    ("验证容器", "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'"),
    ("测试SearXNG", "curl -s -o /dev/null -w 'HTTP %{http_code}' 'http://localhost:8888/search?q=test&format=json'"),
    ("测试RSSHub", "curl -s -o /dev/null -w 'HTTP %{http_code}' 'http://localhost:1200/'"),
]

for label, cmd in cmds:
    print(f"\n[{label}]")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print(f"  {out}")
    if err and "warning" not in err.lower():
        print(f"  ERR: {err}")

client.close()
print("\n部署完成！")
print("SearXNG: http://8.137.174.58:8888")
print("RSSHub:  http://8.137.174.58:1200")
