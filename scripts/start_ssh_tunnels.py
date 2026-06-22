"""
start_ssh_tunnels.py — 使用系统 OpenSSH 建立端口转发隧道
用法: python scripts/start_ssh_tunnels.py
后台运行，按 Ctrl+C 退出。
"""
import subprocess
import sys
import signal
import time

REMOTE_HOST = "8.137.174.58"
SSH_USER = "root"

FORWARDS = [
    # local_port, remote_host, remote_port
    (5432, "127.0.0.1", 5432),   # PostgreSQL
    (6379, "127.0.0.1", 6379),   # Redis
    (8000, "127.0.0.1", 8000),   # ChromaDB
]


def main():
    # Build ssh command with port forwards
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no",
           "-o", "ServerAliveInterval=30",
           "-o", "ServerAliveCountMax=3",
           "-N"]  # no remote command

    for lp, rh, rp in FORWARDS:
        cmd.extend(["-L", f"{lp}:{rh}:{rp}"])

    cmd.append(f"{SSH_USER}@{REMOTE_HOST}")

    print(f"[SSH-Tunnel] 启动 OpenSSH 隧道 → {REMOTE_HOST}")
    for lp, rh, rp in FORWARDS:
        print(f"  localhost:{lp} → {rh}:{rp}")
    print("按 Ctrl+C 退出...")

    proc = subprocess.Popen(cmd)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        print("\n[SSH-Tunnel] 已关闭")


if __name__ == "__main__":
    main()
