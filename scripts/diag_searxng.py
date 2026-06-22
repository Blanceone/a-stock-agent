"""诊断并修复 SearXNG 搜索引擎配置"""
import paramiko
import time
import json

HOST = "8.137.174.58"
USER = "root"
PASSWD = "lxh107016!"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWD, timeout=15)

def run_cmd(cmd):
    stdin, stdout, stderr = client.exec_command(cmd)
    return stdout.read().decode().strip()

# 1. 查看当前配置
print("=" * 60)
print("[1] 当前 settings.yml:")
print("=" * 60)
print(run_cmd("cat /opt/astock/searxng/settings.yml"))

# 2. 测试搜索（直接 curl）
print("\n" + "=" * 60)
print("[2] 测试搜索 (中文 query):")
print("=" * 60)
result = run_cmd("curl -s 'http://localhost:8888/search?q=%E5%9B%BA%E6%80%81%E7%94%B5%E6%B1%A0&format=json' | python3 -c \"import sys,json; d=json.load(sys.stdin); print(f'结果数: {len(d.get(\\\"results\\\",[]))}'); [print(f'  - {r.get(\\\"title\\\",\\\"\\\")} | {r.get(\\\"url\\\",\\\"\\\")}') for r in d.get('results',[])[:5]]\"")
print(result)

# 3. 查看可用引擎
print("\n" + "=" * 60)
print("[3] 查看已启用引擎:")
print("=" * 60)
engines_info = run_cmd("curl -s 'http://localhost:8888/config' | python3 -c \"import sys,json; d=json.load(sys.stdin); engines=d.get('engines',[]); [print(f'  {e[\\\"name\\\"]:20s} cat={e.get(\\\"categories\\\",\\\"\\\")}') for e in engines[:30]]\"")
print(engines_info)

# 4. 查看容器日志
print("\n" + "=" * 60)
print("[4] 容器日志 (最近 30 行):")
print("=" * 60)
print(run_cmd("docker logs astock_searxng --tail 30 2>&1"))

client.close()
