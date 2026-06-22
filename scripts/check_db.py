"""检查 stock_basic 表结构和数据"""
import sys
sys.path.insert(0, r"D:\work\ai\project-one")
from src.infrastructure.database import init_all, get_pg_conn, release_pg_conn

init_all()
conn = get_pg_conn()
cur = conn.cursor()

cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='stock_basic' ORDER BY ordinal_position")
for r in cur.fetchall():
    print(r)

print("---")
cur.execute("SELECT ts_code, name, circ_mv, industry FROM stock_basic LIMIT 5")
for r in cur.fetchall():
    print(r)

print("---")
cur.execute("SELECT COUNT(*) FROM stock_basic WHERE circ_mv IS NOT NULL AND circ_mv > 0")
print("circ_mv > 0:", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM stock_basic WHERE industry IS NOT NULL AND industry != ''")
print("with industry:", cur.fetchone()[0])

release_pg_conn(conn)
