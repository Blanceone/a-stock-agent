"""修复 SearXNG 配置，启用 JSON API"""
import paramiko
import time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("8.137.174.58", username="root", password="lxh107016!", timeout=15)

settings = """use_default_settings: true

general:
  instance_name: "AStock SearXNG"

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "zh"
  formats:
    - html
    - json

server:
  secret_key: "astock_searxng_secret_2024"
  limiter: false
  image_proxy: false
"""

print("[1] 更新 settings.yml (启用 json 格式)...")
stdin, stdout, stderr = client.exec_command(
    f"cat > /opt/astock/searxng/settings.yml << 'SETTINGSEOF'\n{settings}SETTINGSEOF"
)
stdout.read()

print("[2] 重启 SearXNG...")
stdin, stdout, stderr = client.exec_command("docker restart astock_searxng")
stdout.read()

print("[3] 等待启动 (8s)...")
time.sleep(8)

print("[4] 测试 JSON API...")
stdin, stdout, stderr = client.exec_command(
    "curl -s -o /dev/null -w 'HTTP %{http_code}' 'http://localhost:8888/search?q=test&format=json'"
)
result = stdout.read().decode().strip()
print(f"  结果: {result}")

if "200" in result:
    print("\nSearXNG JSON API 已启用!")
else:
    print(f"\n仍然返回 {result}，查看容器日志...")
    stdin, stdout, stderr = client.exec_command("docker logs astock_searxng --tail 20")
    print(stdout.read().decode().strip())

client.close()
