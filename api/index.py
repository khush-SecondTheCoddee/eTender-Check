import os
import sqlite3
from typing import Optional, List
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    root_path="/api",
    docs_url="/docs",
    openapi_url="/openapi.json"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Look for the DB inside the deployed API bundle folder context
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "tenders.db")

class TenderResponse(BaseModel):
    id: str
    title: str
    authority: str
    type: str
    status: str
    value: float
    close_date: str
    description: str

class PaginatedTenderResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[TenderResponse]

@app.get("/tenders", response_model=PaginatedTenderResponse)
def get_tenders(
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=100),
    search: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    min_value: Optional[float] = Query(None),
    max_value: Optional[float] = Query(None),
    sort_by: str = Query("id"),
    sort_order: str = Query("ASC")
):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    params = []
    
    # Core architectural fix: Qualify target fields to prevent ambiguous column conflicts during FTS5 joins
    if search and search.strip():
        clean_search = search.replace("'", "").replace('"', "") + "*"
        base_query = "FROM tenders t JOIN tenders_fts f ON t.rowid = f.rowid WHERE tenders_fts MATCH ?"
        params.append(clean_search)
    else:
        base_query = "FROM tenders t WHERE 1=1"

    if type:
        base_query += " AND t.type = ?"
        params.append(type)
    if status:
        base_query += " AND t.status = ?"
        params.append(status)
    if min_value is not None:
        base_query += " AND t.value >= ?"
        params.append(min_value)
    if max_value is not None:
        base_query += " AND t.value <= ?"
        params.append(max_value)

    allowed_sort_columns = ["id", "title", "authority", "type", "status", "value", "close_date"]
    if sort_by not in allowed_sort_columns:
        sort_by = "id"
    sort_order = "DESC" if sort_order.upper() == "DESC" else "ASC"

    cursor.execute(f"SELECT COUNT(*) {base_query}", params)
    total_records = cursor.fetchone()[0]

    # Calculate exact page boundaries offsets
    offset = (page - 1) * limit
    
    # Core architectural fix: Hardcode integer limits into string queries 
    # to bypass strict parameter type bindings constraints in SQLite serverless runs
    data_query = f"""
        SELECT t.id, t.title, t.authority, t.type, t.status, t.value, t.close_date, t.description 
        {base_query} 
        ORDER BY t.{sort_by} {sort_order} 
        LIMIT {int(limit)} OFFSET {int(offset)}
    """
    
    cursor.execute(data_query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {"total": total_records, "page": page, "limit": limit, "results": results}
