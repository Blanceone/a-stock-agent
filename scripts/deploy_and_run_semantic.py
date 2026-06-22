"""
deploy_and_run_semantic.py — 在远程服务器上直接运行语义初始化
1. 通过 paramiko SSH 建立隧道（仅用于文件传输）
2. 将必要代码和模型传到远程
3. 在远程直接运行（本地 PG/ChromaDB 连接，无隧道问题）
"""
import paramiko
import os
import time

REMOTE_HOST = "8.137.174.58"
SSH_USER = "root"
SSH_PASS = "lxh107016!"
REMOTE_DIR = "/opt/astock/semantic_init"


def run_cmd(ssh, cmd, timeout=30):
    """Execute command and return stdout"""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    return out, err


def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(REMOTE_HOST, username=SSH_USER, password=SSH_PASS)
    print("[1] Connected to remote server")

    # Create remote directory
    run_cmd(ssh, f"mkdir -p {REMOTE_DIR}")

    # Check what's already on the server
    out, _ = run_cmd(ssh, f"ls {REMOTE_DIR}/")
    print(f"[2] Remote dir contents: {out.strip()}")

    # Check if Python and deps are available
    out, err = run_cmd(ssh, "python3 --version 2>&1")
    print(f"[3] Python: {out.strip()}")

    out, err = run_cmd(ssh, "pip3 list 2>/dev/null | grep -iE 'psycopg2|chromadb|sentence|requests|loguru|tushare|pydantic'")
    print(f"[4] Installed packages:\n{out}")

    # Check if PG is accessible locally on remote
    out, err = run_cmd(ssh, "PGPASSWORD=astock psql -U astock -d astock -h 127.0.0.1 -c 'SELECT COUNT(*) FROM stock_basic' 2>&1")
    print(f"[5] PG local test: {out.strip()} {err.strip()[:200]}")

    # Check ChromaDB
    out, err = run_cmd(ssh, "curl -s http://127.0.0.1:8000/api/v1/heartbeat 2>&1")
    print(f"[6] ChromaDB: {out.strip()[:100]}")

    ssh.close()


if __name__ == "__main__":
    main()
