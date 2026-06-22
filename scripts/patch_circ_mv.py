"""补录 stock_basic.circ_mv (流通市值) — 从 Tushare daily_basic 获取"""
import sys
import time
sys.path.insert(0, r"D:\work\ai\project-one")

from dotenv import load_dotenv
load_dotenv(r"D:\work\ai\project-one\.env")

from src.infrastructure.database import init_all, get_pg_conn, release_pg_conn
from config.settings import settings
import tushare as ts

print("[1/4] 初始化数据库连接...")
init_all()
conn = get_pg_conn()

print("[2/4] 获取最近交易日...")
pro = ts.pro_api(settings.tushare_token)

# 获取最近交易日
from datetime import datetime, timedelta
today = datetime.now()
trade_date = today.strftime("%Y%m%d")
# 尝试获取当天数据，若为空则回退
for i in range(10):
    d = (today - timedelta(days=i)).strftime("%Y%m%d")
    df_test = pro.daily_basic(trade_date=d, fields="ts_code,circ_mv")
    if df_test is not None and len(df_test) > 0:
        trade_date = d
        break
    time.sleep(0.3)

print(f"  最近交易日: {trade_date}")

print("[3/4] 拉取全A股流通市值 (daily_basic)...")
df = pro.daily_basic(trade_date=trade_date, fields="ts_code,circ_mv")
if df is None or df.empty:
    print("  ERROR: daily_basic 返回空数据")
    sys.exit(1)

# circ_mv 单位：万元，过滤掉 NaN
df = df.dropna(subset=["circ_mv"])
print(f"  获取到 {len(df)} 只股票的 circ_mv")

print("[4/4] 批量 UPDATE stock_basic...")
cur = conn.cursor()
updated = 0
batch_size = 500

# 分批更新
for start in range(0, len(df), batch_size):
    batch = df.iloc[start:start + batch_size]
    values = [(row["circ_mv"], row["ts_code"]) for _, row in batch.iterrows()]
    # 使用 executemany 批量更新
    cur.executemany(
        "UPDATE stock_basic SET circ_mv = %s WHERE ts_code = %s",
        values,
    )
    updated += len(values)
    if (start + batch_size) % 2000 == 0:
        conn.commit()
        print(f"  已更新 {updated} / {len(df)}...")
    time.sleep(0.1)

conn.commit()

# 验证
cur.execute("SELECT COUNT(*) FROM stock_basic WHERE circ_mv IS NOT NULL AND circ_mv > 0")
count_with_mv = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM stock_basic")
total = cur.fetchone()[0]
print(f"\n  更新完成: {updated} 行已写入")
print(f"  验证: {count_with_mv} / {total} 只股票有 circ_mv 数据")

# 采样展示
cur.execute("SELECT ts_code, name, circ_mv FROM stock_basic WHERE circ_mv > 0 ORDER BY circ_mv DESC LIMIT 5")
print("\n  流通市值 Top 5:")
for row in cur.fetchall():
    mv_yi = float(row[2]) / 10000  # 万元 → 亿元
    print(f"    {row[0]} {row[1]}: {mv_yi:.0f} 亿元")

cur.close()
release_pg_conn(conn)
print("\n补录完成!")
