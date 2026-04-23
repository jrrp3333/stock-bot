import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional, Union

from researcher import research_failure


DB_PATH = Path(__file__).resolve().parent / "trades.db"


def initialize_schema(db_path: Union[str, Path] = DB_PATH) -> None:
    """Create (or migrate) the bot tables used for entries and closed trades."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                entry_price REAL NOT NULL,
                qty REAL NOT NULL,
                tp_pct REAL,
                sl_pct REAL,
                tp_price REAL,
                sl_price REAL,
                requested_notional REAL,
                approved_notional REAL,
                entry_reason TEXT,
                signal_source TEXT NOT NULL,
                order_id TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                qty REAL NOT NULL,
                signal_source TEXT NOT NULL,
                pnl_percent REAL NOT NULL,
                failure_notes TEXT
            )
            """
        )

        # Backfill the column for older DBs that were created before notes existed.
        cur.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cur.fetchall()}
        if "failure_notes" not in columns:
            cur.execute("ALTER TABLE trades ADD COLUMN failure_notes TEXT")

        # Backfill risk-plan columns for older DBs.
        cur.execute("PRAGMA table_info(trade_entries)")
        entry_columns = {row[1] for row in cur.fetchall()}
        if "tp_pct" not in entry_columns:
            cur.execute("ALTER TABLE trade_entries ADD COLUMN tp_pct REAL")
        if "sl_pct" not in entry_columns:
            cur.execute("ALTER TABLE trade_entries ADD COLUMN sl_pct REAL")
        if "tp_price" not in entry_columns:
            cur.execute("ALTER TABLE trade_entries ADD COLUMN tp_price REAL")
        if "sl_price" not in entry_columns:
            cur.execute("ALTER TABLE trade_entries ADD COLUMN sl_price REAL")
        if "requested_notional" not in entry_columns:
            cur.execute("ALTER TABLE trade_entries ADD COLUMN requested_notional REAL")
        if "approved_notional" not in entry_columns:
            cur.execute("ALTER TABLE trade_entries ADD COLUMN approved_notional REAL")
        if "entry_reason" not in entry_columns:
            cur.execute("ALTER TABLE trade_entries ADD COLUMN entry_reason TEXT")

        conn.commit()
    finally:
        conn.close()


def log_entry(
    ticker: str,
    entry_price: float,
    qty: float,
    signal_source: str,
    tp_pct: Optional[float] = None,
    sl_pct: Optional[float] = None,
    tp_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    requested_notional: Optional[float] = None,
    approved_notional: Optional[float] = None,
    entry_reason: Optional[str] = None,
    order_id: Optional[str] = None,
    timestamp: Optional[Union[datetime, str]] = None,
    db_path: Union[str, Path] = DB_PATH,
) -> int:
    """Log a buy/entry event and return the inserted row id."""
    initialize_schema(db_path)

    if timestamp is None:
        entry_dt = datetime.now(UTC)
    elif isinstance(timestamp, datetime):
        entry_dt = timestamp
    else:
        try:
            entry_dt = datetime.fromisoformat(timestamp)
        except ValueError:
            entry_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO trade_entries (
                timestamp,
                ticker,
                entry_price,
                qty,
                tp_pct,
                sl_pct,
                tp_price,
                sl_price,
                requested_notional,
                approved_notional,
                entry_reason,
                signal_source,
                order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_dt.isoformat(timespec="seconds"),
                ticker.upper(),
                float(entry_price),
                float(qty),
                float(tp_pct) if tp_pct is not None else None,
                float(sl_pct) if sl_pct is not None else None,
                float(tp_price) if tp_price is not None else None,
                float(sl_price) if sl_price is not None else None,
                float(requested_notional) if requested_notional is not None else None,
                float(approved_notional) if approved_notional is not None else None,
                entry_reason,
                signal_source,
                order_id,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def log_trade(
    ticker: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    signal_source: str,
    timestamp: Optional[Union[datetime, str]] = None,
    db_path: Union[str, Path] = DB_PATH,
) -> int:
    """
    Log a closed trade and return the inserted row id.
    Automatically researches losses and stores failure notes.
    """
    initialize_schema(db_path)

    if timestamp is None:
        close_dt = datetime.now(UTC)
    elif isinstance(timestamp, datetime):
        close_dt = timestamp
    else:
        try:
            close_dt = datetime.fromisoformat(timestamp)
        except ValueError:
            close_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")

    pnl_percent = ((exit_price - entry_price) / entry_price) * 100

    failure_notes = None
    if pnl_percent < 0:
        failure_notes = research_failure(ticker=ticker, close_date=close_dt)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO trades (
                timestamp,
                ticker,
                entry_price,
                exit_price,
                qty,
                signal_source,
                pnl_percent,
                failure_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                close_dt.isoformat(timespec="seconds"),
                ticker.upper(),
                float(entry_price),
                float(exit_price),
                float(qty),
                signal_source,
                float(pnl_percent),
                failure_notes,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()
