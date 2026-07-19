import os
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(docs_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SELF-HEALING DATABASE ROUTING CHANNELS ---
REPO_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "tenders.db")
TMP_DB_PATH = "/tmp/tenders.db"

def init_and_seed_tmp_db(db_path: str):
    """Initializes schema and injects mock data if running inside a fresh /tmp folder context."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenders (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, authority TEXT NOT NULL,
            type TEXT NOT NULL, status TEXT NOT NULL, value REAL NOT NULL,
            close_date TEXT NOT NULL, description TEXT
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_type ON tenders(type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_status ON tenders(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_value ON tenders(value);")

    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS tenders_fts USING fts5(
            id UNINDEXED, title, authority, description, content='tenders', content_rowid='rowid'
        );
    """)
    
    # Triggers for FTS5 synchronization
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS tenders_ai AFTER INSERT ON tenders BEGIN
            INSERT INTO tenders_fts(rowid, id, title, authority, description)
            VALUES (new.rowid, new.id, new.title, new.authority, new.description);
        END;
    """)

    # Check if data already exists
    cursor.execute("SELECT COUNT(*) FROM tenders;")
    if cursor.fetchone()[0] == 0:
        authorities = ["Dept of Transportation", "Federal Health Agency", "Defense Logistics Agency", "Bureau of Technology"]
        types = ["Works", "Services", "Supplies", "Consultancy"]
        statuses = ["Active", "Awarded", "Under Evaluation", "Archived"]
        nouns = ["Cloud Infrastructure", "Highway Expansion", "Medical Equipment", "Cybersecurity Audit"]
        modifiers = ["Deployment", "Modernization", "Maintenance Contract", "Feasibility Study"]

        batch_data = []
        start_date = datetime.now()

        for i in range(1, 201):  # Compact batch allocation size for quick serverless execution setups
            t_id = f"TEN-2026-{i:04d}"
            title = f"{random.choice(nouns)} {random.choice(modifiers)}"
            auth = random.choice(authorities)
            t_type = random.choice(types)
            status = random.choice(statuses)
            value = round(random.uniform(50000, 5000000), 2)
            close_date = (start_date + timedelta(days=random.randint(-10, 60))).strftime("%Y-%m-%d")
            desc = f"Comprehensive architecture design requirements for the {title} project framework."
            batch_data.append((t_id, title, auth, t_type, status, value, close_date, desc))

        cursor.executemany("""
            INSERT OR IGNORE INTO tenders (id, title, authority, type, status, value, close_date, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, batch_data)
        
    conn.commit()
    conn.close()

def get_db_connection():
    """Resolves read-only or writable database connections gracefully based on runtime state."""
    # Scenario A: Database was committed to Git repo -> Open directly in Read-Only Mode
    if os.path.exists(REPO_DB_PATH):
        return sqlite3.connect(f"file:{REPO_DB_PATH}?mode=ro", uri=True)
    
    # Scenario B: No database found -> Spin up and seed dynamically in Vercel's writable /tmp space
    init_and_seed_tmp_db(TMP_DB_PATH)
    return sqlite3.connect(TMP_DB_PATH)

# --- SCHEMAS ---
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

# --- CONTROLLER ---
@app.get("/{path:path}", response_model=PaginatedTenderResponse)
def catch_all_tenders(
    path: str,
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
    if "tenders" not in path.lower() and path != "":
        raise HTTPException(status_code=404, detail="Resource path not found.")

    try:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        params = []
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

        offset = (page - 1) * limit
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

    except Exception as db_err:
        # Intercept database errors cleanly to reveal the trace inside the JSON output
        raise HTTPException(status_code=500, detail=f"Database Archiver Context Fault: {str(db_err)}")
