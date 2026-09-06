"""
Lokální SQLite databáze srsni-data.

Celý systém stojí na jednom souboru srsni.db (viz DB_PATH) - do něj sync.py
a live.py ukládají data, mcp_server.py z něj čte. Soubor se necommituje
do gitu (je v .gitignore), protože jde o generovaná/proměnlivá data.

Dvě tabulky:

- matches: jeden řádek na zápas, identifikovaný nbl_id (ID z nbl.basketball).
  Drží i fiba_id (jakmile ho známe), stav zápasu a kdy byl naposledy
  synchronizovaný.

- snapshots: historie stažených dat.json v čase. NIKDY se nepřepisují,
  jen přidávají (append-only) - u živého zápasu tak postupně vzniká
  časová řada, ze které jde třeba dopočítat průběh utkání.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).parent / "srsni.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    nbl_id INTEGER PRIMARY KEY,
    fiba_id INTEGER,
    season TEXT,
    date_utc TEXT,
    home_team TEXT,
    away_team TEXT,
    status TEXT NOT NULL DEFAULT 'upcoming',
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(nbl_id),
    fetched_at TEXT NOT NULL,
    clock TEXT,
    period INTEGER,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_match_id ON snapshots(match_id);
"""


def now_iso() -> str:
    """Aktuální čas v UTC jako ISO string - takhle časy ukládáme všude v DB."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Otevře spojení na DB, zajistí schéma a při odchodu commitne/zavře."""
    if db_path is None:
        # Vyhodnoceno až tady (ne jako výchozí hodnota parametru), aby šlo
        # DB_PATH přepsat i po importu modulu (např. v testech).
        db_path = DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@dataclass
class Match:
    nbl_id: int
    fiba_id: int | None
    season: str | None
    date_utc: str | None
    home_team: str | None
    away_team: str | None
    status: str
    last_synced_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Match":
        return cls(**{key: row[key] for key in row.keys()})


def upsert_match(
    conn: sqlite3.Connection,
    nbl_id: int,
    *,
    fiba_id: int | None = None,
    season: str | None = None,
    date_utc: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    status: str | None = None,
) -> None:
    """
    Vloží zápas, nebo (pokud už existuje) aktualizuje jen ta pole, která
    dostaneme vyplněná (None znamená "neměnit"). Tím může sync.py volat
    tuhle funkci opakovaně, jak postupně zjišťuje víc informací.
    """
    existing = conn.execute("SELECT * FROM matches WHERE nbl_id = ?", (nbl_id,)).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO matches (nbl_id, fiba_id, season, date_utc, home_team, away_team, status)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, 'upcoming'))
            """,
            (nbl_id, fiba_id, season, date_utc, home_team, away_team, status),
        )
        return

    merged = {
        "fiba_id": fiba_id if fiba_id is not None else existing["fiba_id"],
        "season": season if season is not None else existing["season"],
        "date_utc": date_utc if date_utc is not None else existing["date_utc"],
        "home_team": home_team if home_team is not None else existing["home_team"],
        "away_team": away_team if away_team is not None else existing["away_team"],
        "status": status if status is not None else existing["status"],
    }
    conn.execute(
        """
        UPDATE matches
        SET fiba_id = ?, season = ?, date_utc = ?, home_team = ?, away_team = ?, status = ?
        WHERE nbl_id = ?
        """,
        (*merged.values(), nbl_id),
    )


def mark_synced(conn: sqlite3.Connection, nbl_id: int) -> None:
    """Zaznamená, že zápas byl právě zkontrolovaný/stažený."""
    conn.execute(
        "UPDATE matches SET last_synced_at = ? WHERE nbl_id = ?",
        (now_iso(), nbl_id),
    )


def get_match(conn: sqlite3.Connection, nbl_id: int) -> Match | None:
    row = conn.execute("SELECT * FROM matches WHERE nbl_id = ?", (nbl_id,)).fetchone()
    return Match.from_row(row) if row else None


def get_match_by_fiba_id(conn: sqlite3.Connection, fiba_id: int) -> Match | None:
    row = conn.execute("SELECT * FROM matches WHERE fiba_id = ?", (fiba_id,)).fetchone()
    return Match.from_row(row) if row else None


def list_matches(conn: sqlite3.Connection, season: str | None = None) -> list[Match]:
    """Vrátí zápasy seřazené podle data, volitelně jen pro danou sezónu."""
    if season is None:
        rows = conn.execute("SELECT * FROM matches ORDER BY date_utc").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM matches WHERE season = ? ORDER BY date_utc", (season,)
        ).fetchall()
    return [Match.from_row(row) for row in rows]


def get_latest_match(conn: sqlite3.Connection) -> Match | None:
    """
    Vrátí "poslední" zápas - přednostně živý, jinak nejnovější odehraný,
    jinak úplně nejnovější podle data. Používá se pro match_ref="latest"
    v mcp_server.py.
    """
    row = conn.execute(
        "SELECT * FROM matches WHERE status = 'live' ORDER BY date_utc DESC LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM matches WHERE status = 'finished' ORDER BY date_utc DESC LIMIT 1"
        ).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM matches ORDER BY date_utc DESC LIMIT 1").fetchone()
    return Match.from_row(row) if row else None


def add_snapshot(
    conn: sqlite3.Connection,
    match_id: int,
    raw_json: dict[str, Any],
    *,
    clock: str | None = None,
    period: int | None = None,
) -> int:
    """Přidá nový snapshot (nikdy nepřepisuje starší). Vrací id nového řádku."""
    cursor = conn.execute(
        """
        INSERT INTO snapshots (match_id, fetched_at, clock, period, raw_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (match_id, now_iso(), clock, period, json.dumps(raw_json, ensure_ascii=False)),
    )
    return cursor.lastrowid


def get_latest_snapshot(conn: sqlite3.Connection, match_id: int) -> dict[str, Any] | None:
    """Vrátí (naparsovaný) raw_json posledního snapshotu daného zápasu, nebo None."""
    row = conn.execute(
        "SELECT raw_json FROM snapshots WHERE match_id = ? ORDER BY id DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    return json.loads(row["raw_json"]) if row else None


def has_snapshot_today(conn: sqlite3.Connection, match_id: int) -> bool:
    """Zjistí, jestli už dnes (UTC) existuje aspoň jeden snapshot zápasu."""
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT 1 FROM snapshots WHERE match_id = ? AND fetched_at LIKE ? LIMIT 1",
        (match_id, f"{today}%"),
    ).fetchone()
    return row is not None
