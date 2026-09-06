"""
live.py - sledování právě probíhajícího zápasu naživo.

Použití (z příkazové řádky):

    uv run python3 live.py                 # najde dnešní zápas Sršňů sám
    uv run python3 live.py 492428           # sleduje konkrétní nbl_id

Jak to funguje: pár minut před naplánovaným výkopem (LEAD_MINUTES) začne
opakovaně stahovat data.json v intervalu POLL_INTERVAL_SECONDS a každé
stažení uloží jako nový snapshot (viz db.add_snapshot - nikdy se
nepřepisuje, jen přidává, takže vzniká historie vývoje zápasu).

Zastaví se, jakmile několikrát POCTA_KONCE za sebou zjistí, že zápas
skončil (stats.is_match_finished), nebo po bezpečnostním stropu
SAFETY_CAP_HOURS od startu - aby to neběželo do nekonečna, kdyby byl
zápas zrušený/odložený a nikdy nedoběhl do stavu "finished".
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

import db
import resolve
import stats
from discovery import USER_AGENT
from sync import fetch_fiba_data

POLL_INTERVAL_SECONDS = 25
LEAD_MINUTES = 5
SAFETY_CAP_HOURS = 3
FINISHED_CONFIRMATIONS_NEEDED = 3


def find_todays_match(conn) -> db.Match | None:
    """Najde zápas Sršňů, který má podle rozpisu výkop dnes a ještě neskončil."""
    today = datetime.now(timezone.utc).date()
    candidates = [
        m
        for m in db.list_matches(conn)
        if m.date_utc and datetime.fromisoformat(m.date_utc).date() == today and m.status != "finished"
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda m: m.date_utc)


def _sleep_until(target: datetime, chunk_seconds: int = 60) -> None:
    """Počká do daného času, v menších kouscích (ať je vidět, že to nezamrzlo)."""
    while True:
        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(chunk_seconds, remaining))


def watch_match(
    nbl_id: int,
    *,
    poll_interval_seconds: int = POLL_INTERVAL_SECONDS,
    lead_minutes: int = LEAD_MINUTES,
    safety_cap_hours: float = SAFETY_CAP_HOURS,
    finished_confirmations_needed: int = FINISHED_CONFIRMATIONS_NEEDED,
    db_path=db.DB_PATH,
) -> None:
    """Hlavní smyčka živého sledování jednoho zápasu."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    with db.connect(db_path) as conn:
        match = db.get_match(conn, nbl_id)
        if match is None:
            raise ValueError(f"Zápas nbl_id={nbl_id} není v databázi - spusť nejdřív sync.py.")
        fiba_id = match.fiba_id
        kickoff_str = match.date_utc

    if fiba_id is None:
        # Poslední pokus dohledat fiba_id, kdyby se objevil až teď.
        fiba_id = resolve.get_fiba_id(nbl_id, session)
        if fiba_id is None:
            raise ValueError(
                f"Zápas nbl_id={nbl_id} ještě nemá přiřazený FIBA feed - zkus to blíž k termínu."
            )
        with db.connect(db_path) as conn:
            db.upsert_match(conn, nbl_id, fiba_id=fiba_id)

    if kickoff_str:
        kickoff = datetime.fromisoformat(kickoff_str)
        start_watching_at = kickoff - timedelta(minutes=lead_minutes)
        if datetime.now(timezone.utc) < start_watching_at:
            print(f"Čekám do {start_watching_at.isoformat()} (výkop {kickoff.isoformat()})...")
            _sleep_until(start_watching_at)

    started_at = datetime.now(timezone.utc)
    deadline = started_at + timedelta(hours=safety_cap_hours)
    consecutive_finished = 0

    print(f"Sleduji zápas nbl_id={nbl_id} (fiba_id={fiba_id})...")

    while datetime.now(timezone.utc) < deadline:
        try:
            raw_json = fetch_fiba_data(fiba_id, session)
        except requests.RequestException as error:
            print(f"Stažení dat se nepovedlo ({error}), zkusím to znovu za {poll_interval_seconds}s.")
            time.sleep(poll_interval_seconds)
            continue

        finished_now = stats.is_match_finished(raw_json)
        with db.connect(db_path) as conn:
            db.add_snapshot(
                conn, nbl_id, raw_json, clock=raw_json.get("clock"), period=raw_json.get("period")
            )
            db.upsert_match(conn, nbl_id, status="finished" if finished_now else "live")
            db.mark_synced(conn, nbl_id)

        print(
            f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
            f"perioda {raw_json.get('period')}, čas {raw_json.get('clock')}"
        )

        if finished_now:
            consecutive_finished += 1
            if consecutive_finished >= finished_confirmations_needed:
                print("Zápas skončil (potvrzeno opakovaně). Konec sledování.")
                return
        else:
            consecutive_finished = 0

        time.sleep(poll_interval_seconds)

    print("Dosažen bezpečnostní strop sledování (zápas se pravděpodobně nekonal). Konec.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        target_nbl_id = int(sys.argv[1])
    else:
        with db.connect() as conn:
            todays_match = find_todays_match(conn)
        if todays_match is None:
            print("Dnes nemají Sršni podle databáze žádný neodehraný zápas. Spusť nejdřív sync.py.")
            sys.exit(1)
        target_nbl_id = todays_match.nbl_id

    watch_match(target_nbl_id)
