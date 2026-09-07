"""数据库方言映射表。

新增方言时在这里加一行即可，四个引擎字段分别是：
- sqllineage: SQLFluff 方言名（不支持时解析器会自动回退 ANSI）
- sqlglot:    sqlglot 方言名
- formatter:  前端 sql-formatter 的语言名
"""

DIALECTS = [
    {"key": "mysql",      "label": "MySQL",       "sqllineage": "mysql",      "sqlglot": "mysql",      "formatter": "mysql"},
    {"key": "postgresql", "label": "PostgreSQL",  "sqllineage": "postgres",   "sqlglot": "postgres",   "formatter": "postgresql"},
    {"key": "oracle",     "label": "Oracle",      "sqllineage": "oracle",     "sqlglot": "oracle",     "formatter": "plsql"},
    {"key": "sqlserver",  "label": "SQL Server",  "sqllineage": "tsql",       "sqlglot": "tsql",       "formatter": "transactsql"},
    {"key": "hive",       "label": "Hive",        "sqllineage": "hive",       "sqlglot": "hive",       "formatter": "hive"},
    {"key": "spark",      "label": "Spark SQL",   "sqllineage": "sparksql",   "sqlglot": "spark",      "formatter": "spark"},
    {"key": "clickhouse", "label": "ClickHouse",  "sqllineage": "clickhouse", "sqlglot": "clickhouse", "formatter": "clickhouse"},
    {"key": "sqlite",     "label": "SQLite",      "sqllineage": "sqlite",     "sqlglot": "sqlite",     "formatter": "sqlite"},
    {"key": "mariadb",    "label": "MariaDB",     "sqllineage": "mariadb",    "sqlglot": "mariadb",    "formatter": "mariadb"},
    {"key": "trino",      "label": "Trino/Presto","sqllineage": "trino",      "sqlglot": "trino",      "formatter": "trino"},
    {"key": "duckdb",     "label": "DuckDB",      "sqllineage": "duckdb",     "sqlglot": "duckdb",     "formatter": "duckdb"},
    {"key": "bigquery",   "label": "BigQuery",    "sqllineage": "bigquery",   "sqlglot": "bigquery",   "formatter": "bigquery"},
    {"key": "db2",        "label": "DB2",         "sqllineage": "db2",        "sqlglot": "db2",        "formatter": "db2"},
    {"key": "ansi",       "label": "ANSI (通用)",  "sqllineage": "ansi",       "sqlglot": None,         "formatter": "sql"},
]

DIALECT_MAP = {d["key"]: d for d in DIALECTS}
DEFAULT_DIALECT = "mysql"


def get_dialect(key: str) -> dict:
    return DIALECT_MAP.get(key, DIALECT_MAP[DEFAULT_DIALECT])
