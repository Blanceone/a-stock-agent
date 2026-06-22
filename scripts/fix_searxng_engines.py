"""修复 SearXNG: 禁用被墙引擎，仅启用国内可访问的搜索引擎"""
import paramiko
import time

HOST = "8.137.174.58"
USER = "root"
PASSWD = "lxh107016!"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWD, timeout=15)

def run_cmd(cmd):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    return stdout.read().decode().strip()

# 新配置: 仅启用国内可访问的搜索引擎
settings = r"""use_default_settings: true

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
  request_timeout: 8

engines:
  # === 国内可用引擎 ===
  - name: baidu
    engine: baidu
    shortcut: bd
    disabled: false
    timeout: 6

  - name: 360search
    engine: 360search
    shortcut: 360
    disabled: false
    timeout: 6

  - name: bing
    engine: bing
    shortcut: bi
    disabled: false
    timeout: 6

  - name: sogou
    engine: sogou
    shortcut: sg
    disabled: false
    timeout: 6

  # === 禁用被墙引擎 ===
  - name: google
    engine: google
    disabled: true

  - name: duckduckgo
    engine: duckduckgo
    disabled: true

  - name: wikipedia
    engine: wikipedia
    disabled: true

  - name: startpage
    engine: startpage
    disabled: true

  - name: brave
    engine: brave
    disabled: true

  - name: qwant
    engine: qwant
    disabled: true

  - name: mojeek
    engine: mojeek
    disabled: true
"""

print("[1/4] 写入新 settings.yml (仅国内引擎)...")
run_cmd(f"cat > /opt/astock/searxng/settings.yml << 'SETTINGSEOF'\n{settings}\nSETTINGSEOF")
print("  OK")

print("[2/4] 重启 SearXNG...")
run_cmd("docker restart astock_searxng")
print("  OK, 等待 10s 启动...")
time.sleep(10)

# 3. 测试搜索
print("[3/4] 测试搜索 '固态电池 产业链'...")
test_result = run_cmd(
    "curl -s 'http://localhost:8888/search?q=%E5%9B%BA%E6%80%81%E7%94%B5%E6%B1%A0+%E4%BA%A7%E4%B8%9A%E9%93%BE&format=json' "
    "| python3 -c \"import sys,json; d=json.load(sys.stdin); results=d.get('results',[]); print(f'结果数: {len(results)}'); "
    "[print(f'  [{i+1}] {r.get(\\\"title\\\",\\\"\\\")} | {r.get(\\\"url\\\",\\\"\\\")}') for i,r in enumerate(results[:5])]\""
)
print(test_result)

# 4. 再测试一个搜索
print("\n[4/4] 测试搜索 '央行降准 银行股'...")
test_result2 = run_cmd(
    "curl -s 'http://localhost:8888/search?q=%E5%A4%AE%E8%A1%8C%E9%99%8D%E5%87%86+%E9%93%B6%E8%A1%8C%E8%82%A1&format=json' "
    "| python3 -c \"import sys,json; d=json.load(sys.stdin); results=d.get('results',[]); print(f'结果数: {len(results)}'); "
    "[print(f'  [{i+1}] {r.get(\\\"title\\\",\\\"\\\")} | {r.get(\\\"url\\\",\\\"\\\")}') for i,r in enumerate(results[:5])]\""
)
print(test_result2)

client.close()
print("\n" + "=" * 50)
print("SearXNG 修复完成!")
