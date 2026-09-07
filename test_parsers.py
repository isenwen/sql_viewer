# -*- coding: utf-8 -*-
"""解析器冒烟测试：python test_parsers.py"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
from backend.parsers import run_chain, AllParsersFailed

CASES = [
    ("基础 INSERT SELECT + JOIN", "mysql", """
INSERT INTO dw.orders_agg
SELECT o.order_id, o.user_id, u.name, o.amount * 1.1 AS amt
FROM ods.orders o
LEFT JOIN dim.users u ON o.user_id = u.id
WHERE o.dt = '2026-01-01'
"""),
    ("CTE + 多语句", "mysql", """
WITH active AS (
  SELECT id, name FROM dim.users WHERE status = 1
)
INSERT INTO dw.vip_users
SELECT a.id, a.name, p.total
FROM active a
JOIN agg.pay p ON a.id = p.uid;

INSERT INTO tmp.x SELECT id FROM dim.users;
"""),
    ("CREATE TABLE AS SELECT", "hive", """
CREATE TABLE dws.sales_summary AS
SELECT s.shop_id, s.city, SUM(s.amount) AS total_amt, COUNT(*) AS cnt
FROM ods.sales s
GROUP BY s.shop_id, s.city
"""),
    ("SELECT * 星号", "mysql", """
INSERT INTO db.tgt
SELECT * FROM db.src
"""),
    ("UPDATE", "postgresql", """
UPDATE dw.users u
SET city_name = c.name, level = l.lv
FROM dim.city c, dim.level l
WHERE u.city_id = c.id AND u.level_id = l.id
"""),
    ("非法 SQL", "mysql", """
THIS IS NOT SQL AT ALL !!!
"""),
]

for name, dialect, sql in CASES:
    print("=" * 70)
    print(f"用例: {name} ({dialect})")
    try:
        graph, parser, attempts = run_chain(sql, dialect, "auto")
        print(f"  [成功] 引擎={parser}")
        for t in graph["tables"]:
            print(f"    表 {t['id']} ({t['role']}) 列: {t['columns']}")
        print(f"    表边: {[(e['source'], e['target']) for e in graph['table_edges']]}")
        print(f"    字段边:")
        for e in graph["column_edges"]:
            print(f"      {e['source_table']}.{e['source_column']} -> {e['target_table']}.{e['target_column']}")
        for w in graph["warnings"]:
            print(f"    [警告] {w}")
    except AllParsersFailed as e:
        print("  [全部失败]")
        for a in e.attempts:
            print(f"    {a['parser']}: {a['message'][:120]}")
