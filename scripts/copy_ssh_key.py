"""
copy_ssh_key.py — 将 SSH 公钥复制到阿里云服务器
"""
import paramiko
import os

PUB_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa.pub")
SERVER = "8.137.174.58"
USER = "root"
PASSWORD = "lxh107016!"

pub_key = open(PUB_KEY_PATH).read().strip()
print(f"[copy_ssh_key] 连接到 {SERVER}...")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)

commands = [
    "mkdir -p /root/.ssh",
    f'echo "{pub_key}" >> /root/.ssh/authorized_keys',
    "chmod 600 /root/.ssh/authorized_keys",
    "echo SSH_KEY_COPIED_OK",
    "docker --version 2>/dev/null || echo NO_DOCKER",
    "docker compose version 2>/dev/null || echo NO_COMPOSE",
]

for cmd in commands:
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print(out)
    if err and "warning" not in err.lower():
        print(f"STDERR: {err}")

client.close()
print("[copy_ssh_key] 完成")
