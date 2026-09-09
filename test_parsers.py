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
    ("DataX: column 按位对齐 + 注释/尾逗号", "mysql", """{
  // 作业配置，含注释与尾逗号的宽容解析
  "job": {
    "setting": { "speed": { "channel": 3 }, },
    "content": [
      {
        "reader": {
          "name": "mysqlreader",
          "parameter": {
            "username": "etl",
            "password": "******",
            "column": ["id", "user_id", "amount"],
            "splitPk": "id",
            "connection": [{
              "jdbcUrl": ["jdbc:mysql://127.0.0.1:3306/ods?useSSL=false"],
              "table": ["orders"]
            }],
            "where": "dt = '2026-01-01'"
          }
        },
        "writer": {
          "name": "mysqlwriter",
          "parameter": {
            "username": "etl",
            "password": "******",
            "column": ["order_id", "uid", "amt"],
            "connection": [{
              "jdbcUrl": ["jdbc:mysql://127.0.0.1:3306/dw"],
              "table": ["orders_sync"]
            }]
          }
        }
      }
    ]
  }
}"""),
    ("DataX: querySql JOIN + HDFS writer", "mysql", """{
  "job": {
    "content": [
      {
        "reader": {
          "name": "mysqlreader",
          "parameter": {
            "querySql": [
              "SELECT o.id AS order_id, u.name AS user_name, o.amount FROM orders o LEFT JOIN users u ON o.user_id = u.id WHERE o.dt = ${bdp.system.bizdate}"
            ],
            "connection": [{
              "jdbcUrl": ["jdbc:mysql://127.0.0.1:3306/ods"],
              "querySql": ["SELECT o.id AS order_id, u.name AS user_name, o.amount FROM orders o LEFT JOIN users u ON o.user_id = u.id"]
            }]
          }
        },
        "writer": {
          "name": "hdfswriter",
          "parameter": {
            "defaultFS": "hdfs://nn:8020",
            "path": "/user/hive/warehouse/dw.db/order_detail/dt=2026-01-01",
            "fileName": "part",
            "writeMode": "append",
            "fieldDelimiter": "\\u0001",
            "column": [
              {"name": "order_id", "type": "bigint"},
              {"name": "user_name", "type": "string"},
              {"name": "amount", "type": "double"}
            ]
          }
        }
      }
    ]
  }
}"""),
    ("DataX: HDFS -> MySQL 通配字段", "mysql", """{
  "job": {
    "content": [
      {
        "reader": {
          "name": "hdfsreader",
          "parameter": {
            "defaultFS": "hdfs://nn:8020",
            "path": "/user/hive/warehouse/ods.db/orders/*",
            "fileType": "orc",
            "fieldDelimiter": "\\u0001",
            "column": ["*"]
          }
        },
        "writer": {
          "name": "mysqlwriter",
          "parameter": {
            "column": ["*"],
            "connection": [{
              "jdbcUrl": ["jdbc:mysql://127.0.0.1:3306/backup"],
              "table": ["orders_bak"]
            }]
          }
        }
      }
    ]
  }
}"""),
    ("Druid: MySQL INSERT SELECT + JOIN", "mysql", """
INSERT INTO dw.orders_agg
SELECT o.order_id, o.user_id, u.name, o.amount * 1.1 AS amt
FROM ods.orders o
LEFT JOIN dim.users u ON o.user_id = u.id
WHERE o.dt = '2026-01-01'
""", "druid"),
    ("Druid: PostgreSQL UPDATE FROM", "postgresql", """
UPDATE dw.users u
SET city_name = c.name, level = l.lv
FROM dim.city c, dim.level l
WHERE u.city_id = c.id AND u.level_id = l.id
""", "druid"),
]

for name, dialect, sql, *pref in CASES:
    pref = pref[0] if pref else "auto"
    print("=" * 70)
    print(f"用例: {name} ({dialect})")
    try:
        graph, parser, attempts = run_chain(sql, dialect, pref)
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
