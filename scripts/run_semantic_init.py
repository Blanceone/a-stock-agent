"""
run_semantic_init.py — 独立运行语义知识库初始化
绕过 main.py 的连接池冲突问题。

用法: python scripts/run_semantic_init.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src.infrastructure.database import init_all
from src.infrastructure.semantic_init import run as semantic_run

print("初始化基础设施...")
init_all()
print("启动语义知识库初始化...")

stats = semantic_run()
print(f"\n===== 完成 =====")
print(f"总计: {stats['total']}")
print(f"成功: {stats['success']}")
print(f"失败: {stats['failed']}")
print(f"跳过: {stats['skipped']}")
