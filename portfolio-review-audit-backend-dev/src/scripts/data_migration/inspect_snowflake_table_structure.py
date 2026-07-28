from __future__ import annotations

import json
from typing import Any

from src.configs.env import settings


DATABASE = "PEAKXV_WISDOM"
SCHEMA = "PR_APP"
TABLE = "PR_AUDIT_PULSE_COMPANY_MASTER"
FULL_TABLE_NAME = f"{DATABASE}.{SCHEMA}.{TABLE}"


def _get_snowflake_conn():
    """Open a Snowflake connection using the app's configured key-pair auth."""
    import snowflake.connector  # type: ignore[import]
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        load_pem_private_key,
    )

    private_key = load_pem_private_key(
        settings.SNOWFLAKE_PASSWORD.encode(),
        password=None,
        backend=default_backend(),
    )
    private_key_der = private_key.private_bytes(
        encoding=Encoding.DER,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )

    return snowflake.connector.connect(
        user=settings.SNOWFLAKE_USER,
        account=settings.SNOWFLAKE_ACCOUNT,
        warehouse=settings.SNOWFLAKE_WAREHOUSE,
        database=DATABASE,
        schema=SCHEMA,
        private_key=private_key_der,
    )


def _fetch_all_dicts(cursor: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor.execute(query, params)
    columns = [col[0].lower() for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _format_type(column: dict[str, Any]) -> str:
    data_type = str(column["data_type"])

    if column["character_maximum_length"] is not None:
        return f"{data_type}({column['character_maximum_length']})"
    if column["numeric_precision"] is not None:
        return f"{data_type}({column['numeric_precision']},{column['numeric_scale']})"
    if column["datetime_precision"] is not None and data_type.upper() in {
        "TIME",
        "TIMESTAMP",
        "TIMESTAMP_LTZ",
        "TIMESTAMP_NTZ",
        "TIMESTAMP_TZ",
    }:
        return f"{data_type}({column['datetime_precision']})"

    return data_type


def inspect_table_structure() -> None:
    required = [
        settings.SNOWFLAKE_USER,
        settings.SNOWFLAKE_PASSWORD,
        settings.SNOWFLAKE_ACCOUNT,
        settings.SNOWFLAKE_WAREHOUSE,
    ]
    if not all(required):
        raise RuntimeError(
            "Snowflake credentials are not configured. Set SNOWFLAKE_USER, "
            "SNOWFLAKE_PASSWORD, SNOWFLAKE_ACCOUNT, and SNOWFLAKE_WAREHOUSE."
        )

    conn = _get_snowflake_conn()
    try:
        cursor = conn.cursor()
        print(f"\nInspecting: {FULL_TABLE_NAME}\n")

        ddl_rows = _fetch_all_dicts(
            cursor,
            "SELECT GET_DDL('TABLE', %s) AS ddl",
            (FULL_TABLE_NAME,),
        )
        print("=== TABLE DDL ===")
        print(ddl_rows[0]["ddl"])

        columns = _fetch_all_dicts(
            cursor,
            """
            SELECT
                ordinal_position,
                column_name,
                data_type,
                character_maximum_length,
                numeric_precision,
                numeric_scale,
                datetime_precision,
                is_nullable,
                column_default,
                comment
            FROM PEAKXV_WISDOM.INFORMATION_SCHEMA.COLUMNS
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (SCHEMA, TABLE),
        )

        print("\n=== COLUMN STRUCTURE JSON ===")
        print(json.dumps(columns, indent=2, default=str))

        print("\n=== COLUMN SUMMARY ===")
        for column in columns:
            nullable = "NULL" if column["is_nullable"] == "YES" else "NOT NULL"
            default = f" DEFAULT {column['column_default']}" if column["column_default"] else ""
            print(
                f"{column['ordinal_position']:>3}. "
                f"{column['column_name']} {_format_type(column)} {nullable}{default}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    inspect_table_structure()
