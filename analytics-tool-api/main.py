from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
import os
import re
import json
import psycopg
from psycopg.rows import dict_row

app = FastAPI(
    title="Analytics Tool API",
    version="1.0.0",
    description="Read-only PostgreSQL analytics tool server for Open WebUI"
)

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|do|vacuum|analyze|comment|refresh|merge)\b",
    re.I,
)

class SqlRequest(BaseModel):
    sql: str

def auth_check(authorization: str | None = Header(default=None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is missing")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth scheme")

    expected_token = os.environ.get("TOOL_BEARER_TOKEN", "").strip()
    received_token = authorization[len("Bearer "):].strip()

    if not expected_token:
        raise HTTPException(status_code=500, detail="TOOL_BEARER_TOKEN is not configured")

    if received_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid token")

def validate_sql(sql: str) -> str:
    q = sql.strip().rstrip(";")
    if not re.match(r"(?is)^(with|select)\b", q):
        raise HTTPException(status_code=400, detail="Only SELECT/WITH statements are allowed")
    if FORBIDDEN.search(q):
        raise HTTPException(status_code=400, detail="Only read-only SQL is allowed")
    if ";" in q:
        raise HTTPException(status_code=400, detail="Only one SQL statement is allowed")
    return q

def get_conn():
    try:
        return psycopg.connect(
            host=os.environ["DB_HOST"],
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            sslmode="require",
            row_factory=dict_row
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB connection error: {str(e)}")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/schema/catalog", summary="Return schema catalog and metric rules")
def schema_catalog():
    with open("catalog.json", "r", encoding="utf-8") as f:
        return json.load(f)

@app.post("/sql/query", dependencies=[Depends(auth_check)], summary="Execute one read-only SQL query")
def sql_query(body: SqlRequest):
    try:
        q = validate_sql(body.sql)
        row_limit = int(os.getenv("ROW_LIMIT", "500"))
        timeout_ms = int(os.getenv("SQL_TIMEOUT_MS", "15000"))
        schema = os.getenv("DB_SCHEMA", "analytics")

        wrapped = f'select * from ({q}) x limit {row_limit}'

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"set local statement_timeout = {int(timeout_ms)}")
            cur.execute("set local default_transaction_read_only = on")
            cur.execute(f'set local search_path to "{schema}", public')
            cur.execute(wrapped)
            rows = cur.fetchall()
            return {
                "row_count": len(rows),
                "rows": rows
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution error: {str(e)}")