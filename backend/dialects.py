"""数据库方言映射表。

新增方言时在这里加一行即可，引擎字段分别是：
- sqllineage: SQLFluff 方言名（不支持时解析器会自动回退 ANSI）
- sqlglot:    sqlglot 方言名
- formatter:  前端 sql-formatter 的语言名
- druid:      阿里 Druid SQL Parser 的 dbType（不支持时解析器回退 mysql）
"""

DIALECTS = [
    {"key": "mysql",      "label": "MySQL",       "sqllineage": "mysql",      "sqlglot": "mysql",      "formatter": "mysql",       "druid": "mysql"},
    {"key": "postgresql", "label": "PostgreSQL",  "sqllineage": "postgres",   "sqlglot": "postgres",   "formatter": "postgresql",  "druid": "postgresql"},
    {"key": "oracle",     "label": "Oracle",      "sqllineage": "oracle",     "sqlglot": "oracle",     "formatter": "plsql",       "druid": "oracle"},
    {"key": "sqlserver",  "label": "SQL Server",  "sqllineage": "tsql",       "sqlglot": "tsql",       "formatter": "transactsql", "druid": "sqlserver"},
    {"key": "hive",       "label": "Hive",        "sqllineage": "hive",       "sqlglot": "hive",       "formatter": "hive",        "druid": "hive"},
    {"key": "spark",      "label": "Spark SQL",   "sqllineage": "sparksql",   "sqlglot": "spark",      "formatter": "spark",       "druid": "hive"},
    {"key": "clickhouse", "label": "ClickHouse",  "sqllineage": "clickhouse", "sqlglot": "clickhouse", "formatter": "clickhouse",  "druid": "clickhouse"},
    {"key": "sqlite",     "label": "SQLite",      "sqllineage": "sqlite",     "sqlglot": "sqlite",     "formatter": "sqlite",      "druid": "sqlite"},
    {"key": "mariadb",    "label": "MariaDB",     "sqllineage": "mariadb",    "sqlglot": "mariadb",    "formatter": "mariadb",     "druid": "mysql"},
    {"key": "trino",      "label": "Trino/Presto","sqllineage": "trino",      "sqlglot": "trino",      "formatter": "trino",       "druid": "presto"},
    {"key": "duckdb",     "label": "DuckDB",      "sqllineage": "duckdb",     "sqlglot": "duckdb",     "formatter": "duckdb",      "druid": "postgresql"},
    {"key": "bigquery",   "label": "BigQuery",    "sqllineage": "bigquery",   "sqlglot": "bigquery",   "formatter": "bigquery",    "druid": "mysql"},
    {"key": "db2",        "label": "DB2",         "sqllineage": "ansi",       "sqlglot": "db2",        "formatter": "db2",         "druid": "db2"},
    {"key": "ansi",       "label": "ANSI (通用)",  "sqllineage": "ansi",       "sqlglot": None,         "formatter": "sql",         "druid": "mysql"},
]

DIALECT_MAP = {d["key"]: d for d in DIALECTS}
DEFAULT_DIALECT = "mysql"


def get_dialect(key: str) -> dict:
    return DIALECT_MAP.get(key, DIALECT_MAP[DEFAULT_DIALECT])
