"""
env_check.py — 分布式环境自检

每个模块启动前只检查自身所需环境条件，不满足时自动修复：
  - SSH 隧道端口不通 → 自动启动 SSH 隧道
  - 端口被占用 → 强制 kill 占用进程并释放

用法（在各模块入口调用）：
  from src.infrastructure.env_check import ensure_env
  ensure_env(need_pg=True, need_redis=True)  # 仅检查所需项
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ── 端口检测 ──────────────────────────────────────────────────────────────────

def check_port_open(port: int, timeout: float = 2.0) -> bool:
    """TCP connect 检测本地端口是否可达（有服务监听）"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_pid_for_port(port: int) -> str | None:
    """查找占用指定端口的进程 PID"""
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                f"netstat -ano | findstr :{port}",
                shell=True, text=True, encoding="gbk", errors="replace",
            )
            for line in out.strip().split("\n"):
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return parts[-1]
        else:
            out = subprocess.check_output(
                f"lsof -ti :{port}", shell=True, text=True,
            ).strip()
            return out if out else None
    except Exception:
        pass
    return None


def kill_port(port: int) -> bool:
    """强制释放端口（kill 占用进程），返回是否成功"""
    pid = _find_pid_for_port(port)
    if not pid:
        return True  # 本就空闲
    try:
        if sys.platform == "win32":
            subprocess.run(
                f"taskkill /PID {pid} /F",
                shell=True, capture_output=True, timeout=5,
            )
        else:
            import os, signal
            os.kill(int(pid), signal.SIGKILL)
        logger.info("[EnvCheck] 已终止占用端口 {} 的进程 PID={}", port, pid)
        return True
    except Exception as e:
        logger.warning("[EnvCheck] 终止端口 {} 占用进程失败: {}", port, e)
        return False


# ── SSH 隧道 ──────────────────────────────────────────────────────────────────

def ensure_ssh_tunnel(needed_ports: list[int]) -> None:
    """
    确保所需的 SSH 隧道端口可达。
    若任一端口不通，启动 SSH 隧道脚本并等待最多 10 秒。
    """
    failed = [p for p in needed_ports if not check_port_open(p)]
    if not failed:
        logger.debug("[EnvCheck] SSH 隧道端口全部可达: {}", needed_ports)
        return

    logger.info("[EnvCheck] 端口 {} 不通，正在启动 SSH 隧道...", failed)
    script = str(PROJECT_ROOT / "scripts" / "start_ssh_tunnels.py")

    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["cmd", "/c", "start", "SSH-Tunnel", "cmd", "/k",
                 sys.executable, script],
                cwd=str(PROJECT_ROOT),
            )
        else:
            subprocess.Popen(
                [sys.executable, script],
                cwd=str(PROJECT_ROOT),
                start_new_session=True,
            )
    except Exception as e:
        logger.warning("[EnvCheck] SSH 隧道启动失败: {}", e)
        return

    # 等待端口可达（最多 10 秒）
    for i in range(10):
        time.sleep(1)
        still_bad = [p for p in failed if not check_port_open(p)]
        if not still_bad:
            logger.info("[EnvCheck] SSH 隧道已建立 ({}s)", i + 1)
            return

    logger.warning("[EnvCheck] SSH 隧道启动超时，部分端口仍不可达: {}", still_bad)


# ── 编排入口 ──────────────────────────────────────────────────────────────────

def ensure_env(
    need_pg: bool = False,
    need_redis: bool = False,
    need_chromadb: bool = False,
    need_port_8088: bool = False,
) -> None:
    """
    模块启动前的分布式自检入口，不满足时自动修复。

    - SSH 隧道端口需要「可达」（有服务监听）
    - 端口 8088 需要「空闲」（无服务，可绑定）
    """
    # 1. 收集需要检查的隧道端口
    tunnel_ports: list[int] = []
    if need_pg:
        tunnel_ports.append(5432)
    if need_redis:
        tunnel_ports.append(6379)
    if need_chromadb:
        tunnel_ports.append(8000)

    if tunnel_ports:
        ensure_ssh_tunnel(tunnel_ports)

    # 2. 端口 8088 空闲检查（仅 api.py 需要）
    if need_port_8088:
        pid = _find_pid_for_port(8088)
        if pid:
            logger.info("[EnvCheck] 端口 8088 被 PID={} 占用，正在释放...", pid)
            kill_port(8088)
            time.sleep(1)
