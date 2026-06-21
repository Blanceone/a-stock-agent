"""
从 C:/Users/13979/Desktop/notes/apis.txt 读取 key:value 对，
写入 .env 文件供 python-dotenv 加载。
每次启动前执行一次即可。
"""
import re
from pathlib import Path

APIS_FILE = Path(r"C:/Users/13979/Desktop/notes/apis.txt")
ENV_FILE = Path(".env")

KEY_MAP = {
    "tushare token":     "TUSHARE_TOKEN",
    "deepseek api":      "DEEPSEEK_API_KEY",
    "github token":      "GITHUB_TOKEN",
    "阿里云服务器公网IP": "ALIYUN_IP",
}


def load_env() -> None:
    text = APIS_FILE.read_text(encoding="utf-8")
    env_lines: list[str] = []
    for raw_key, env_key in KEY_MAP.items():
        pattern = rf"{re.escape(raw_key)}[：:]\s*(\S+)"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            env_lines.append(f'{env_key}="{m.group(1)}"')
    ENV_FILE.write_text("\n".join(env_lines), encoding="utf-8")
    print(f"[load_env] 已写入 {len(env_lines)} 个环境变量到 .env")


if __name__ == "__main__":
    load_env()
