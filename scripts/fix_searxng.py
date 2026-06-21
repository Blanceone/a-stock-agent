"""修复 SearXNG 端口冲突并重启"""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("8.137.174.58", username="root", password="lxh107016!", timeout=15)

cmds = [
    ("检查8080端口占用", "ss -tlnp | grep 8080 || echo '端口未占用'"),
    ("清理旧容器", "docker stop astock_searxng 2>/dev/null; docker rm astock_searxng 2>/dev/null; echo OK"),
    ("重启SearXNG", "cd /opt/astock && docker compose up -d searxng 2>&1"),
    ("等待启动", "sleep 8"),
    ("验证服务", "docker ps --format 'table {{.Names}}\\t{{.Status}}'"),
    ("测试SearXNG", "curl -s -o /dev/null -w 'HTTP %{http_code}' 'http://localhost:8080/search?q=test&format=json' || echo 'SearXNG not ready yet'"),
    ("测试RSSHub", "curl -s -o /dev/null -w 'HTTP %{http_code}' 'http://localhost:1200/' || echo 'RSSHub not ready'"),
]

for label, cmd in cmds:
    print(f"\n[{label}]")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print(f"  {out}")
    if err and "warning" not in err.lower():
        print(f"  ERR: {err}")

client.close()
print("\n完成！")
