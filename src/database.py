"""
Database Storage Module
Supports BOTH SQLite (local dev) and MySQL (MySQL Workbench / production).

Switch by setting DB_TYPE = "mysql" or "sqlite"
"""

import json
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG — Change these to match your MySQL Workbench
# ─────────────────────────────────────────────

DB_TYPE = "mysql"   # "sqlite" or "mysql"

MYSQL_CONFIG = {
    "host":     "localhost",      # MySQL Workbench host (usually localhost)
    "port":     3306,             # Default MySQL port
    "user":     "root",           # Your MySQL username
    "password": "Aditya@9386",  # Your MySQL password
    "database": "engineering_ocr" # DB name (auto-created if not exists)
}

SQLITE_PATH = "output/engineering_drawings.db"


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

SQLITE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS drawings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_file     TEXT NOT NULL,
        processed_at    TEXT,
        pages_processed INTEGER,
        processing_time REAL,
        title           TEXT,
        drawing_id      TEXT,
        current_revision TEXT,
        drawn_by        TEXT,
        checked_by      TEXT,
        approved_by     TEXT,
        drawing_date    TEXT,
        scale           TEXT,
        sheet           TEXT,
        finish          TEXT,
        material        TEXT,
        company         TEXT,
        other_meta_json TEXT,
        overall_score   REAL
    );
    CREATE TABLE IF NOT EXISTS revision_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        drawing_fk  INTEGER,
        rev         TEXT,
        description TEXT,
        date        TEXT,
        approved_by TEXT
    );
    CREATE TABLE IF NOT EXISTS component_list (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        drawing_fk  INTEGER,
        item_no     TEXT,
        part_number TEXT,
        description TEXT,
        quantity    TEXT,
        material    TEXT,
        notes       TEXT
    );
    CREATE TABLE IF NOT EXISTS other_data (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        drawing_fk   INTEGER,
        data_type    TEXT,
        content_json TEXT
    );
    CREATE TABLE IF NOT EXISTS accuracy_metrics (
        id                         INTEGER PRIMARY KEY AUTOINCREMENT,
        drawing_fk                 INTEGER,
        metadata_completeness      REAL,
        revision_row_completeness  REAL,
        component_row_completeness REAL,
        revision_rows_extracted    INTEGER,
        component_rows_extracted   INTEGER,
        parse_errors               INTEGER,
        overall_score              REAL,
        processing_time_seconds    REAL
    );
"""

MYSQL_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS drawings (
        id               INT AUTO_INCREMENT PRIMARY KEY,
        source_file      VARCHAR(500) NOT NULL,
        processed_at     VARCHAR(50),
        pages_processed  INT,
        processing_time  FLOAT,
        title            VARCHAR(500),
        drawing_id       VARCHAR(100),
        current_revision VARCHAR(20),
        drawn_by         VARCHAR(200),
        checked_by       VARCHAR(200),
        approved_by      VARCHAR(200),
        drawing_date     VARCHAR(50),
        scale            VARCHAR(50),
        sheet            VARCHAR(50),
        finish           VARCHAR(200),
        material         VARCHAR(200),
        company          VARCHAR(200),
        other_meta_json  TEXT,
        overall_score    FLOAT
    ) ENGINE=InnoDB""",

    """CREATE TABLE IF NOT EXISTS revision_history (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        drawing_fk  INT,
        rev         VARCHAR(20),
        description TEXT,
        date        VARCHAR(50),
        approved_by VARCHAR(200),
        FOREIGN KEY (drawing_fk) REFERENCES drawings(id) ON DELETE CASCADE
    ) ENGINE=InnoDB""",

    """CREATE TABLE IF NOT EXISTS component_list (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        drawing_fk  INT,
        item_no     VARCHAR(50),
        part_number VARCHAR(200),
        description TEXT,
        quantity    VARCHAR(50),
        material    VARCHAR(200),
        notes       TEXT,
        FOREIGN KEY (drawing_fk) REFERENCES drawings(id) ON DELETE CASCADE
    ) ENGINE=InnoDB""",

    """CREATE TABLE IF NOT EXISTS other_data (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        drawing_fk   INT,
        data_type    VARCHAR(50),
        content_json TEXT,
        FOREIGN KEY (drawing_fk) REFERENCES drawings(id) ON DELETE CASCADE
    ) ENGINE=InnoDB""",

    """CREATE TABLE IF NOT EXISTS accuracy_metrics (
        id                         INT AUTO_INCREMENT PRIMARY KEY,
        drawing_fk                 INT,
        metadata_completeness      FLOAT,
        revision_row_completeness  FLOAT,
        component_row_completeness FLOAT,
        revision_rows_extracted    INT,
        component_rows_extracted   INT,
        parse_errors               INT,
        overall_score              FLOAT,
        processing_time_seconds    FLOAT,
        FOREIGN KEY (drawing_fk) REFERENCES drawings(id) ON DELETE CASCADE
    ) ENGINE=InnoDB"""
]


# ─────────────────────────────────────────────
# Connection helpers
# ─────────────────────────────────────────────

def get_connection(db_type: str = None):
    """Return a DB connection. db_type = 'mysql' or 'sqlite'."""
    db_type = db_type or DB_TYPE
    if db_type == "mysql":
        return _get_mysql_connection()
    return _get_sqlite_connection()


def _get_mysql_connection():
    try:
        import mysql.connector
    except ImportError:
        raise ImportError(
            "\nmysql-connector-python not installed.\n"
            "Run:  pip install mysql-connector-python\n"
        )

    # Connect without specifying DB first, so we can create it if needed
    cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
    conn = mysql.connector.connect(**cfg)
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_CONFIG['database']}`")
    cur.execute(f"USE `{MYSQL_CONFIG['database']}`")

    for sql in MYSQL_SCHEMA:
        cur.execute(sql)

    conn.commit()
    cur.close()
    return conn


def _get_sqlite_connection():
    import sqlite3
    Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SQLITE_SCHEMA)
    conn.commit()
    return conn


def _ph(db_type: str) -> str:
    """SQL placeholder: %s for MySQL, ? for SQLite."""
    return "%s" if db_type == "mysql" else "?"


def _rows(cursor, db_type: str) -> list[dict]:
    """Convert cursor results to list of dicts (MySQL doesn't have row_factory)."""
    if db_type == "mysql":
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    return [dict(r) for r in cursor.fetchall()]


# ─────────────────────────────────────────────
# Store result
# ─────────────────────────────────────────────

def store_result(result: dict, metrics: dict, db_type: str = None) -> int:
    """Insert extracted drawing data into the database. Returns new drawing id."""
    db_type = db_type or DB_TYPE
    p = _ph(db_type)
    conn = get_connection(db_type)
    meta = result.get("drawing_metadata", {})
    cur = conn.cursor()

    # 1 — drawings
    cur.execute(f"""
        INSERT INTO drawings (
            source_file, processed_at, pages_processed, processing_time,
            title, drawing_id, current_revision,
            drawn_by, checked_by, approved_by,
            drawing_date, scale, sheet, finish, material, company,
            other_meta_json, overall_score
        ) VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
    """, (
        result.get("source_file", ""),
        result.get("processed_at", datetime.now().isoformat()),
        result.get("pages_processed", 1),
        result.get("processing_time_seconds", 0),
        meta.get("title", ""),
        meta.get("drawing_id", ""),
        meta.get("current_revision", ""),
        meta.get("drawn_by", ""),
        meta.get("checked_by", ""),
        meta.get("approved_by", ""),
        meta.get("date", ""),
        meta.get("scale", ""),
        meta.get("sheet", ""),
        meta.get("finish", ""),
        meta.get("material", ""),
        meta.get("company", ""),
        json.dumps(meta.get("other_fields", {})),
        metrics.get("overall_score", 0),
    ))
    drawing_id = cur.lastrowid
    conn.commit()

    # 2 — revision_history
    for row in result.get("revision_history", []):
        cur.execute(
            f"INSERT INTO revision_history (drawing_fk, rev, description, date, approved_by) VALUES ({p},{p},{p},{p},{p})",
            (drawing_id, row.get("rev",""), row.get("description",""),
             row.get("date",""), row.get("approved_by",""))
        )

    # 3 — component_list
    for row in result.get("component_list", []):
        cur.execute(
            f"INSERT INTO component_list (drawing_fk, item_no, part_number, description, quantity, material, notes) VALUES ({p},{p},{p},{p},{p},{p},{p})",
            (drawing_id, row.get("item_no",""), row.get("part_number",""),
             row.get("description",""), row.get("quantity",""),
             row.get("material",""), row.get("notes",""))
        )

    # 4 — other_data
    od = result.get("other_data", {})
    for note in od.get("notes", []):
        cur.execute(f"INSERT INTO other_data (drawing_fk, data_type, content_json) VALUES ({p},{p},{p})",
                    (drawing_id, "note", json.dumps(note)))
    for table in od.get("tables", []):
        cur.execute(f"INSERT INTO other_data (drawing_fk, data_type, content_json) VALUES ({p},{p},{p})",
                    (drawing_id, "table", json.dumps(table)))
    if od.get("tolerances"):
        cur.execute(f"INSERT INTO other_data (drawing_fk, data_type, content_json) VALUES ({p},{p},{p})",
                    (drawing_id, "tolerance", json.dumps(od["tolerances"])))

    # 5 — accuracy_metrics
    cur.execute(f"""
        INSERT INTO accuracy_metrics (
            drawing_fk, metadata_completeness, revision_row_completeness,
            component_row_completeness, revision_rows_extracted,
            component_rows_extracted, parse_errors, overall_score, processing_time_seconds
        ) VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p})
    """, (
        drawing_id,
        metrics.get("metadata_completeness", 0),
        metrics.get("revision_history_row_completeness", 0),
        metrics.get("component_list_row_completeness", 0),
        metrics.get("revision_history_rows_extracted", 0),
        metrics.get("component_list_rows_extracted", 0),
        metrics.get("parse_errors", 0),
        metrics.get("overall_score", 0),
        metrics.get("processing_time_seconds", 0),
    ))

    conn.commit()
    cur.close()
    conn.close()
    print(f"  Stored in {db_type.upper()} DB (id={drawing_id}): {result.get('source_file','')}")
    return drawing_id


# ─────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────

def get_all_drawings(db_type: str = None) -> list[dict]:
    db_type = db_type or DB_TYPE
    conn = get_connection(db_type)
    cur = conn.cursor()
    cur.execute("SELECT * FROM drawings ORDER BY id DESC")
    rows = _rows(cur, db_type)
    cur.close(); conn.close()
    return rows


def get_drawing_detail(drawing_id: int, db_type: str = None) -> dict:
    db_type = db_type or DB_TYPE
    p = _ph(db_type)
    conn = get_connection(db_type)
    cur = conn.cursor()

    cur.execute(f"SELECT * FROM drawings WHERE id={p}", (drawing_id,))
    drawing = _rows(cur, db_type)[0]

    cur.execute(f"SELECT * FROM revision_history WHERE drawing_fk={p}", (drawing_id,))
    drawing["revision_history"] = _rows(cur, db_type)

    cur.execute(f"SELECT * FROM component_list WHERE drawing_fk={p}", (drawing_id,))
    drawing["component_list"] = _rows(cur, db_type)

    cur.execute(f"SELECT * FROM other_data WHERE drawing_fk={p}", (drawing_id,))
    drawing["other_data"] = _rows(cur, db_type)

    cur.execute(f"SELECT * FROM accuracy_metrics WHERE drawing_fk={p}", (drawing_id,))
    rows = _rows(cur, db_type)
    drawing["metrics"] = rows[0] if rows else {}

    cur.close(); conn.close()
    return drawing


def search_by_part_number(part_number: str, db_type: str = None) -> list[dict]:
    db_type = db_type or DB_TYPE
    p = _ph(db_type)
    conn = get_connection(db_type)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT d.*, c.part_number, c.description, c.quantity
        FROM drawings d
        JOIN component_list c ON c.drawing_fk = d.id
        WHERE c.part_number LIKE {p}
    """, (f"%{part_number}%",))
    rows = _rows(cur, db_type)
    cur.close(); conn.close()
    return rows


def find_duplicate_parts(db_type: str = None) -> list[dict]:
    db_type = db_type or DB_TYPE
    conn = get_connection(db_type)
    cur = conn.cursor()
    cur.execute("""
        SELECT part_number, description,
               COUNT(DISTINCT drawing_fk) AS drawing_count,
               GROUP_CONCAT(DISTINCT drawing_fk) AS drawing_ids
        FROM component_list
        WHERE part_number != ''
        GROUP BY part_number
        HAVING drawing_count > 1
        ORDER BY drawing_count DESC
    """)
    rows = _rows(cur, db_type)
    cur.close(); conn.close()
    return rows


# ─────────────────────────────────────────────
# Quick connection test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Testing {DB_TYPE.upper()} connection...")
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM drawings")
        count = cur.fetchone()[0]
        print(f"SUCCESS! Connected to {DB_TYPE.upper()}. drawings table has {count} row(s).")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"FAILED: {e}")
