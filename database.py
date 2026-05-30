import sqlite3
import os
import sys
import datetime
import json
import pandas as pd
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

if getattr(sys, 'frozen', False):
    # In a PyInstaller bundle, use the directory of the executable
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # In source mode, use the directory of database.py
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "financial_analyst.db")

def _connect_db() -> sqlite3.Connection:
    """
    Creates and configures a sqlite3 connection.
    Enables WAL mode, normal synchronicity, and a 30-second busy timeout.
    This prevents lock contention and allows concurrent reads and writes.
    """
    # 30-second timeout to wait for locks to clear without crashing
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    
    # Configure high-performance concurrent database parameters
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")  # Write-Ahead Logging
    conn.execute("PRAGMA synchronous = NORMAL;") # Fast and safe WAL writes
    
    return conn

@contextmanager
def db_session():
    """
    Context manager that yields a database connection.
    Automatically commits on success, rolls back on error, and closes the connection.
    """
    conn = _connect_db()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    """Initializes database schema and seeds default instruments."""
    with db_session() as conn:
        cursor = conn.cursor()
        
        # 1. Instruments Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        );
        """)
        
        # 2. Historical Prices Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS historical_prices (
            symbol TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, date),
            FOREIGN KEY (symbol) REFERENCES instruments (symbol) ON DELETE CASCADE
        );
        """)
        
        # 3. Scraped Articles Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scraped_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            url TEXT UNIQUE,
            title TEXT,
            author TEXT,
            pub_date TEXT,
            content TEXT,
            sentiment TEXT,
            summary TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol) REFERENCES instruments (symbol) ON DELETE CASCADE
        );
        """)
        
        # 4. Agent Logs Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            agent TEXT,
            level TEXT,
            message TEXT
        );
        """)
        
        # 5. Analysis Reports Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_reports (
            symbol TEXT PRIMARY KEY,
            report_date TEXT,
            report_content TEXT,
            sentiment_score REAL,
            forecast_json TEXT,
            FOREIGN KEY (symbol) REFERENCES instruments (symbol) ON DELETE CASCADE
        );
        """)
        
        # 6. Connection Settings Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        
        # Seed default instruments if table is empty
        cursor.execute("SELECT COUNT(*) FROM instruments;")
        if cursor.fetchone()[0] == 0:
            default_instruments = [
                ("USDTRY=X", "USD/TRY", "Forex"),
                ("EURUSD=X", "EUR/USD", "Forex"),
                ("GC=F", "Ons Altın (XAU/USD)", "Emtia"),
                ("BZ=F", "Brent Crude Oil", "Emtia"),
                ("BTC-USD", "Bitcoin (BTC/USD)", "Kripto Para"),
                ("^GSPC", "S&P 500", "Küresel Endeks"),
                ("XU100.IS", "BIST 100", "Yerel Endeks"),
                ("NVDA", "NVIDIA Corporation", "Teknoloji Hissesi"),
                ("^TNX", "US 10Y Bond Yield", "Makro Gösterge"),
                ("HG=F", "Bakır Vadeli İşlemleri", "Değerli Metal")
            ]
            cursor.executemany(
                "INSERT OR IGNORE INTO instruments (symbol, name, category, is_active) VALUES (?, ?, ?, 1);",
                default_instruments
            )
            
    # Write initial log outside of context to avoid nesting sessions
    save_log("System", "INFO", "Database initialized and default instruments seeded.")

# --- Instrument CRUD ---

def add_instrument(symbol: str, name: str, category: str) -> bool:
    """Adds a new instrument or updates it if it exists and was inactive."""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO instruments (symbol, name, category, is_active) VALUES (?, ?, ?, 1);",
                (symbol.strip(), name.strip(), category.strip())
            )
        save_log("System", "INFO", f"Instrument added/updated: {symbol} ({name})")
        return True
    except Exception as e:
        save_log("System", "ERROR", f"Failed to add instrument {symbol}: {str(e)}")
        return False

def remove_instrument(symbol: str) -> bool:
    """Removes an instrument from the database."""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM instruments WHERE symbol = ?;", (symbol,))
        save_log("System", "INFO", f"Instrument removed: {symbol}")
        return True
    except Exception as e:
        save_log("System", "ERROR", f"Failed to remove instrument {symbol}: {str(e)}")
        return False

def get_active_instruments() -> List[Dict[str, Any]]:
    """Returns a list of all active instruments."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, name, category, is_active FROM instruments WHERE is_active = 1 ORDER BY category, symbol;")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

# --- Historical Prices ---

def save_prices(symbol: str, df: pd.DataFrame) -> bool:
    """Saves historical prices from a pandas DataFrame to the database."""
    if df.empty:
        return False
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            for index, row in df.iterrows():
                date_str = index.strftime('%Y-%m-%d') if hasattr(index, 'strftime') else str(index)[:10]
                cursor.execute("""
                INSERT OR REPLACE INTO historical_prices (symbol, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (
                    symbol,
                    date_str,
                    float(row['Open']),
                    float(row['High']),
                    float(row['Low']),
                    float(row['Close']),
                    int(row['Volume']) if 'Volume' in row and not pd.isna(row['Volume']) else 0
                ))
            return True
    except Exception as e:
        save_log("Database", "ERROR", f"Failed to save prices for {symbol}: {str(e)}")
        return False

def get_prices(symbol: str) -> pd.DataFrame:
    """Retrieves historical prices for a symbol as a pandas DataFrame."""
    with db_session() as conn:
        query = "SELECT date, open as Open, high as High, low as Low, close as Close, volume as Volume FROM historical_prices WHERE symbol = ? ORDER BY date ASC;"
        df = pd.read_sql_query(query, conn, params=(symbol,))
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
        return df

# --- Scraped Articles ---

def save_article(symbol: str, url: str, title: str, author: str, pub_date: str, content: str, sentiment: str = "NEUTRAL", summary: str = "") -> bool:
    """Saves a scraped article to the database."""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO scraped_articles (symbol, url, title, author, pub_date, content, sentiment, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (symbol, url, title, author, pub_date, content, sentiment, summary))
            return True
    except Exception as e:
        save_log("Database", "ERROR", f"Failed to save article from {url}: {str(e)}")
        return False

def get_articles(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves articles for a specific symbol or all articles if symbol is None."""
    with db_session() as conn:
        cursor = conn.cursor()
        if symbol:
            cursor.execute("SELECT * FROM scraped_articles WHERE symbol = ? ORDER BY created_at DESC;", (symbol,))
        else:
            cursor.execute("SELECT * FROM scraped_articles ORDER BY created_at DESC;")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

# --- Agent Logs ---

def save_log(agent: str, level: str, message: str) -> None:
    """Saves a log entry with a timestamp."""
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        # Use our safe configured connection helper
        conn = _connect_db()
        try:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO agent_logs (timestamp, agent, level, message)
            VALUES (?, ?, ?, ?);
            """, (timestamp, agent, level, message))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"Failed to write log to db: {str(e)}")

def get_logs(limit: int = 150) -> List[Dict[str, Any]]:
    """Retrieves the latest logs."""
    try:
        conn = _connect_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, agent, level, message FROM agent_logs ORDER BY id DESC LIMIT ?;", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in reversed(rows)]
        finally:
            conn.close()
    except Exception as e:
        print(f"Failed to get logs from db: {str(e)}")
        return []

def clear_logs() -> None:
    """Clears all logs in the database."""
    try:
        conn = _connect_db()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM agent_logs;")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

# --- Analysis Reports ---

def save_report(symbol: str, report_content: str, sentiment_score: float, forecast_json: str) -> bool:
    """Saves or updates an analysis report for a symbol."""
    report_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO analysis_reports (symbol, report_date, report_content, sentiment_score, forecast_json)
            VALUES (?, ?, ?, ?, ?);
            """, (symbol, report_date, report_content, sentiment_score, forecast_json))
            return True
    except Exception as e:
        save_log("Database", "ERROR", f"Failed to save report for {symbol}: {str(e)}")
        return False

def get_report(symbol: str) -> Optional[Dict[str, Any]]:
    """Retrieves the latest report for a symbol."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM analysis_reports WHERE symbol = ?;", (symbol,))
        row = cursor.fetchone()
        return dict(row) if row else None

# --- Connection Settings ---

def save_setting(key: str, value: str) -> None:
    """Saves or updates a connection setting in the database."""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);",
                (key.strip(), value.strip())
            )
        save_log("Database", "INFO", f"Saved setting in database: {key}")
    except Exception as e:
        save_log("Database", "ERROR", f"Failed to save setting {key}: {str(e)}")

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieves a setting value from the database."""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?;", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
    except Exception as e:
        save_log("Database", "ERROR", f"Failed to get setting {key}: {str(e)}")
        return default
