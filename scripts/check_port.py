"""检查并释放8080端口，然后重启SearXNG"""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("8.137.174.58", username="root", password="lxh107016!", timeout=15)

cmds = [
    ("查看8080进程", "ps aux | grep -E '8080|searxng|searx' | grep -v grep"),
    ("查看8080进程详情", "ls -la /proc/1253675/exe 2>/dev/null; cat /proc/1253675/cmdline 2>/dev/null | tr '\\0' ' ' || echo 'process not found'"),
    ("检查现有SearXNG API", "curl -s 'http://localhost:8080/search?q=test&format=json' 2>/dev/null | head -c 500 || echo 'no json'"),
]

for label, cmd in cmds:
    print(f"\n[{label}]")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    if out:
        print(f"  {out[:500]}")

client.close()
