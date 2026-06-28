"""
preflight_check.py — 启动前状态检查

用法:
  python scripts/preflight_check.py              # 交互模式（彩色终端）
  python scripts/preflight_check.py --json       # JSON 输出（程序化调用）
  python scripts/preflight_check.py --auto-fix   # 自动修复后重新检查
  python scripts/preflight_check.py --checks ssh,pg,redis  # 仅检查指定项

退出码: 0=全通过, 1=有失败但用户选继续, 2=用户取消
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 颜色输出 ──────────────────────────────────────────────────────────────────
_COLOR = sys.stdout.isatty()


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _COLOR else s


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _COLOR else s


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if _COLOR else s


def _cyan(s: str) -> str:
    return f"\033[36m{s}\033[0m" if _COLOR else s


# ── 检查项定义 ─────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    key: str
    passed: bool
    message: str
    fixable: bool = False
    elapsed_ms: int = 0


@dataclass
class PreflightReport:
    results: list[CheckResult] = field(default_factory=list)
    all_passed: bool = False
    exit_code: int = 0


# ── 单项检查函数 ───────────────────────────────────────────────────────────────

def _check_port(port: int, timeout: float = 2.0) -> tuple[bool, str, int]:
    """尝试连接本地端口，返回 (成功, 描述, 耗时ms)"""
    t0 = time.time()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            ms = int((time.time() - t0) * 1000)
            return True, f"端口可连接 ({ms}ms)", ms
    except OSError:
        ms = int((time.time() - t0) * 1000)
        return False, "连接超时", ms


def check_ssh_pg() -> CheckResult:
    ok, msg, ms = _check_port(5432)
    return CheckResult("SSH隧道 (PG:5432)", "ssh_pg", ok, msg, fixable=not ok, elapsed_ms=ms)


def check_ssh_redis() -> CheckResult:
    ok, msg, ms = _check_port(6379)
    return CheckResult("SSH隧道 (Redis:6379)", "ssh_redis", ok, msg, fixable=not ok, elapsed_ms=ms)


def check_ssh_chromadb() -> CheckResult:
    ok, msg, ms = _check_port(8000)
    return CheckResult("SSH隧道 (ChromaDB:8000)", "ssh_chromadb", ok, msg, fixable=not ok, elapsed_ms=ms)


def check_postgresql() -> CheckResult:
    t0 = time.time()
    try:
        import psycopg2
        conn = psycopg2.connect(
            "postgresql://astock:astock@localhost:5432/astock",
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        ms = int((time.time() - t0) * 1000)
        return CheckResult("PostgreSQL", "pg", True, f"连接成功 ({ms}ms)", elapsed_ms=ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return CheckResult("PostgreSQL", "pg", False, f"连接失败: {e}", elapsed_ms=ms)


def check_redis() -> CheckResult:
    t0 = time.time()
    try:
        import redis
        r = redis.from_url("redis://localhost:6379/0", socket_connect_timeout=3)
        r.ping()
        r.close()
        ms = int((time.time() - t0) * 1000)
        return CheckResult("Redis", "redis", True, f"PING 成功 ({ms}ms)", elapsed_ms=ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return CheckResult("Redis", "redis", False, f"PING 失败: {e}", elapsed_ms=ms)


def check_chromadb() -> CheckResult:
    t0 = time.time()
    try:
        import requests
        resp = requests.get("http://localhost:8000/api/v1/heartbeat", timeout=5)
        resp.raise_for_status()
        ms = int((time.time() - t0) * 1000)
        return CheckResult("ChromaDB", "chromadb", True, f"心跳正常 ({ms}ms)", elapsed_ms=ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return CheckResult("ChromaDB", "chromadb", False, f"心跳失败: {e}", elapsed_ms=ms)


def check_port_8088() -> CheckResult:
    """检查 8088 端口是否空闲（未被占用）"""
    t0 = time.time()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("0.0.0.0", 8088))
        s.close()
        ms = int((time.time() - t0) * 1000)
        return CheckResult("8088端口", "port_8088", True, "端口空闲", elapsed_ms=ms)
    except OSError:
        ms = int((time.time() - t0) * 1000)
        # 尝试找出占用进程 PID
        pid_info = _find_pid_for_port(8088)
        msg = f"端口被占用" + (f" (PID {pid_info})" if pid_info else "")
        return CheckResult("8088端口", "port_8088", False, msg, fixable=True, elapsed_ms=ms)


def check_stock_basic() -> CheckResult:
    t0 = time.time()
    try:
        import psycopg2
        conn = psycopg2.connect(
            "postgresql://astock:astock@localhost:5432/astock",
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM stock_basic")
        cnt = cur.fetchone()[0]
        cur.close()
        conn.close()
        ms = int((time.time() - t0) * 1000)
        if cnt > 0:
            return CheckResult("stock_basic数据", "stock_basic", True, f"共 {cnt} 条记录", elapsed_ms=ms)
        else:
            return CheckResult("stock_basic数据", "stock_basic", False, "表为空，请运行初始化 [2]", elapsed_ms=ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return CheckResult("stock_basic数据", "stock_basic", False, f"查询失败: {e}", elapsed_ms=ms)


def _find_pid_for_port(port: int) -> str | None:
    """在 Windows 上用 netstat 查找占用端口的 PID"""
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                f'netstat -ano | findstr :{port}', shell=True, text=True, encoding="gbk",
                errors="replace",
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


# ── 检查注册表 ─────────────────────────────────────────────────────────────────

ALL_CHECKS: dict[str, callable] = {
    "ssh_pg":       check_ssh_pg,
    "ssh_redis":    check_ssh_redis,
    "ssh_chromadb": check_ssh_chromadb,
    "pg":           check_postgresql,
    "redis":        check_redis,
    "chromadb":     check_chromadb,
    "port_8088":    check_port_8088,
    "stock_basic":  check_stock_basic,
}

CHECK_GROUPS = {
    "ssh": ["ssh_pg", "ssh_redis", "ssh_chromadb"],
}


def _resolve_keys(checks_str: str | None) -> list[str]:
    """将 --checks 参数解析为 key 列表"""
    if not checks_str:
        return list(ALL_CHECKS.keys())
    keys = []
    for name in checks_str.split(","):
        name = name.strip().lower()
        if name in CHECK_GROUPS:
            keys.extend(CHECK_GROUPS[name])
        elif name in ALL_CHECKS:
            keys.append(name)
        else:
            print(f"  [!] 未知检查项: {name}")
    return keys or list(ALL_CHECKS.keys())


# ── 执行检查 ───────────────────────────────────────────────────────────────────

def run_preflight(checks_str: str | None = None) -> PreflightReport:
    keys = _resolve_keys(checks_str)
    report = PreflightReport()
    for key in keys:
        fn = ALL_CHECKS.get(key)
        if fn:
            result = fn()
            report.results.append(result)
    report.all_passed = all(r.passed for r in report.results)
    report.exit_code = 0 if report.all_passed else 1
    return report


# ── 自动修复 ───────────────────────────────────────────────────────────────────

def auto_fix(report: PreflightReport) -> PreflightReport:
    """对可修复项执行自动修复，修复后重新检查"""
    fixable = [r for r in report.results if not r.passed and r.fixable]
    if not fixable:
        print("  无需修复")
        return report

    # 检查 SSH 隧道（三个端口一起修复）
    ssh_failures = [r for r in fixable if r.key.startswith("ssh_")]
    if ssh_failures:
        print(f"\n  [修复] 启动 SSH 隧道...")
        _start_ssh_tunnel()
        time.sleep(3)

    # 检查 8088 端口占用
    port_fail = [r for r in fixable if r.key == "port_8088"]
    if port_fail:
        pid = _find_pid_for_port(8088)
        if pid:
            print(f"\n  [修复] 终止占用 8088 端口的进程 PID={pid}...")
            _kill_pid(pid)
            time.sleep(1)

    # 重新检查
    print("\n  重新检查...")
    return run_preflight()


def _start_ssh_tunnel():
    """在新窗口中启动 SSH 隧道"""
    try:
        script = str(PROJECT_ROOT / "scripts" / "start_ssh_tunnels.py")
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
        print("  SSH 隧道启动请求已发送")
    except Exception as e:
        print(f"  SSH 隧道启动失败: {e}")


def _kill_pid(pid: str):
    """终止指定 PID 的进程"""
    try:
        if sys.platform == "win32":
            subprocess.run(f"taskkill /PID {pid} /F", shell=True, capture_output=True)
        else:
            import signal
            import os
            os.kill(int(pid), signal.SIGTERM)
        print(f"  进程 PID={pid} 已终止")
    except Exception as e:
        print(f"  终止进程失败: {e}")


# ── 交互输出 ───────────────────────────────────────────────────────────────────

def _print_report(report: PreflightReport):
    print()
    for r in report.results:
        if r.passed:
            tag = _green("[PASS]")
        else:
            tag = _red("[FAIL]")
        print(f"  {tag} {r.name:<25} — {r.message}")

    failed = [r for r in report.results if not r.passed]
    if not failed:
        print(f"\n  {_green('✓ 全部检查通过!')}")
    else:
        print(f"\n  {_red(f'─── {len(failed)} 项未通过 ───')}")
        fixable = [r for r in failed if r.fixable]
        if fixable:
            print(f"  {_yellow('[A]')} 自动修复"
                  f" ({', '.join(r.name for r in fixable)})")
        print(f"  {_yellow('[S]')} 跳过检查，强制启动")
        print(f"  {_yellow('[C]')} 取消，返回菜单")


def _interactive_loop(report: PreflightReport) -> int:
    """交互式处理检查结果，返回退出码"""
    if report.all_passed:
        return 0

    failed = [r for r in report.results if not r.passed]
    fixable = [r for r in failed if r.fixable]

    while True:
        print()
        choice = input("  请选择 [A/S/C]: ").strip().upper()

        if choice == "A" and fixable:
            report = auto_fix(report)
            _print_report(report)
            if report.all_passed:
                return 0
            failed = [r for r in report.results if not r.passed]
            fixable = [r for r in failed if r.fixable]
            if not fixable:
                print("  剩余项无法自动修复，请手动处理。")
        elif choice == "S":
            print("  已选择跳过，继续启动。")
            return 1
        elif choice == "C":
            print("  已取消。")
            return 2
        else:
            if not fixable and choice == "A":
                print("  无可自动修复项。")
            print("  无效输入，请重新选择。")


# ── CLI 入口 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="启动前状态检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--auto-fix", action="store_true", help="自动修复可修复项")
    parser.add_argument("--checks", type=str, default=None, help="仅检查指定项，逗号分隔")
    args = parser.parse_args()

    report = run_preflight(args.checks)

    if args.auto_fix and not report.all_passed:
        report = auto_fix(report)

    if args.json:
        # JSON 模式：纯输出
        out = {
            "all_passed": report.all_passed,
            "results": [
                {
                    "name": r.name,
                    "key": r.key,
                    "passed": r.passed,
                    "message": r.message,
                    "fixable": r.fixable,
                    "elapsed_ms": r.elapsed_ms,
                }
                for r in report.results
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(report.exit_code)
    else:
        # 交互模式
        print()
        print("  ─── 启动前状态检查 ───")
        _print_report(report)
        code = _interactive_loop(report)
        sys.exit(code)


if __name__ == "__main__":
    main()
