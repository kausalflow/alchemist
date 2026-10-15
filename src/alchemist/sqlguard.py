"""Read-only SQL guard, using DuckDB's own parser.

A query passes only if it is one SELECT statement whose tables are `ds.<dataset>`
views the run may read (or its own CTEs), with no table functions and no
file-reading functions. Returns the datasets the query reads.
"""

from __future__ import annotations

import json

import duckdb

BANNED_FUNCTIONS = {"glob", "getenv", "current_setting", "query", "query_table", "sniff_csv"}


class SQLRejected(ValueError):
    pass


def check_sql(sql: str, allowed: set[str]) -> list[str]:
    con = duckdb.connect()
    parsed = json.loads(con.execute("select json_serialize_sql(?)", [sql]).fetchone()[0])
    if parsed.get("error"):
        raise SQLRejected(f"only a single SELECT is allowed ({parsed.get('error_message', 'parse error')})")
    statements = parsed["statements"]
    if len(statements) != 1:
        raise SQLRejected("send exactly one SELECT statement")
    used: set[str] = set()
    _walk(statements[0]["node"], allowed, used, ctes=set())
    return sorted(used)


def _walk(node, allowed: set[str], used: set[str], ctes: set[str]) -> None:
    if isinstance(node, list):
        for n in node:
            _walk(n, allowed, used, ctes)
        return
    if not isinstance(node, dict):
        return
    cte_map = node.get("cte_map", {}).get("map", []) if isinstance(node.get("cte_map"), dict) else []
    if cte_map:
        ctes = ctes | {c["key"] for c in cte_map}
    kind = node.get("type")
    if kind == "TABLE_FUNCTION":
        raise SQLRejected("table functions such as read_parquet are not allowed; query ds.<dataset> views")
    if kind == "BASE_TABLE":
        schema, table = node.get("schema_name", ""), node.get("table_name", "")
        catalog = node.get("catalog_name", "")
        if not schema and not catalog and table in ctes:
            pass
        elif schema == "ds" and not catalog and table in allowed:
            used.add(table)
        elif schema == "ds":
            raise SQLRejected(f"dataset ds.{table} is not available to this run (unknown, unapproved or another plant)")
        else:
            raise SQLRejected(f"table {'.'.join(p for p in (catalog, schema, table) if p)} is not allowed; use ds.<dataset> views")
    if node.get("class") == "FUNCTION":
        name = str(node.get("function_name", "")).lower()
        if name.startswith("read_") or name in BANNED_FUNCTIONS:
            raise SQLRejected(f"function {name} is not allowed")
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, allowed, used, ctes)
