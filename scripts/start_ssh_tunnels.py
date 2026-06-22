"""
start_ssh_tunnels.py — SSH 端口转发隧道（自动登录）

用法: python scripts/start_ssh_tunnels.py

优先从 apis.txt 读取凭证自动登录，失败时提示手动输入。
转发端口：PostgreSQL(5432) / Redis(6379) / ChromaDB(8000)
后台运行，按 Ctrl+C 退出。
"""
import sys
import time
import getpass
from pathlib import Path

# ── 默认配置 ──────────────────────────────────────────────────────────────────

DEFAULT_HOST = "8.137.174.58"
DEFAULT_USER = "root"
APIS_TXT = Path(r"C:\Users\13979\Desktop\notes\apis.txt")

FORWARDS = [
    # (local_port, remote_host, remote_port)
    (5432, "127.0.0.1", 5432),   # PostgreSQL
    (6379, "127.0.0.1", 6379),   # Redis
    (8000, "127.0.0.1", 8000),   # ChromaDB
]


def _read_apis_txt() -> tuple:
    """
    从 apis.txt 读取阿里云服务器凭证。
    返回 (host, user, password)，找不到时返回 (None, None, None)。

    目标行格式（tab 分隔）：
      阿里云服务器公网IP：x.x.x.x 实例ID：xxx\t用户名：root\t密码：xxx
    """
    if not APIS_TXT.exists():
        return None, None, None

    host = None
    user = None
    password = None

    try:
        with open(APIS_TXT, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("阿里云服务器"):
                    continue

                # 按 tab 拆分各字段
                segments = [s.strip() for s in line.replace("\t", "\n").split("\n") if s.strip()]

                for seg in segments:
                    if "公网IP" in seg:
                        # 阿里云服务器公网IP：8.137.174.58 实例ID：xxx
                        for sep in ("：", ":"):
                            if sep in seg:
                                ip = seg.split(sep, 1)[1].split()[0]
                                host = ip
                                break
                    elif seg.startswith("用户名"):
                        # 用户名：root
                        for sep in ("：", ":"):
                            if sep in seg:
                                user = seg.split(sep, 1)[1].strip()
                                break
                    elif seg.startswith("密码"):
                        # 密码：lxh107016!
                        for sep in ("：", ":"):
                            if sep in seg:
                                password = seg.split(sep, 1)[1].strip()
                                break

                break  # 只处理第一行匹配
    except Exception:
        pass

    return host, user, password


def _try_connect(host: str, user: str, password: str) -> object:
    """
    尝试创建 SSH 隧道。
    成功返回 SSHTunnelForwarder 实例（已启动），失败返回 None。
    """
    try:
        # paramiko 4.0 移除了 DSSKey，sshtunnel 仍引用，需要补丁
        import paramiko
        if not hasattr(paramiko, "DSSKey"):
            paramiko.DSSKey = paramiko.RSAKey

        from sshtunnel import SSHTunnelForwarder

        remote_binds = [(rh, rp) for _, rh, rp in FORWARDS]
        local_binds = [("127.0.0.1", lp) for lp, _, _ in FORWARDS]

        tunnel = SSHTunnelForwarder(
            (host, 22),
            ssh_username=user,
            ssh_password=password,
            remote_bind_addresses=remote_binds,
            local_bind_addresses=local_binds,
            allow_agent=False,
            host_pkey_directories=[],
        )
        tunnel.start()
        return tunnel
    except Exception as e:
        return None


def main():
    print(f"[SSH-Tunnel] 正在连接远程服务器...")

    tunnel = None
    host = None

    # ── 第一优先：从 apis.txt 自动登录 ────────────────────────────
    auto_host, auto_user, auto_pass = _read_apis_txt()
    if auto_host and auto_pass:
        host = auto_host
        user = auto_user or DEFAULT_USER
        print(f"[SSH-Tunnel] 从 apis.txt 读取凭证 → {user}@{host}")
        tunnel = _try_connect(host, user, auto_pass)
        if tunnel:
            print(f"[SSH-Tunnel] 自动登录成功!")
        else:
            print(f"[SSH-Tunnel] 自动登录失败，需要手动输入凭证")

    # ── 第二优先：手动输入 ────────────────────────────────────────
    if tunnel is None:
        if not host:
            host = input(f"  服务器 IP [{DEFAULT_HOST}]: ").strip() or DEFAULT_HOST
        user = input(f"  用户名 [{DEFAULT_USER}]: ").strip() or DEFAULT_USER
        password = getpass.getpass("  密码: ")

        print(f"[SSH-Tunnel] 尝试连接 {user}@{host}...")
        tunnel = _try_connect(host, user, password)
        if tunnel is None:
            print("[SSH-Tunnel] 连接失败，请检查 IP、用户名和密码")
            sys.exit(1)

    # ── 隧道已建立 ────────────────────────────────────────────────
    print(f"[SSH-Tunnel] 端口转发已建立:")
    for lp, rh, rp in FORWARDS:
        print(f"  localhost:{lp} --> {rh}:{rp}")
    print()
    print("按 Ctrl+C 退出...")

    try:
        while True:
            if not tunnel.is_active:
                print("\n[SSH-Tunnel] 连接已断开")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        tunnel.stop()
        print("\n[SSH-Tunnel] 已关闭")


if __name__ == "__main__":
    main()
