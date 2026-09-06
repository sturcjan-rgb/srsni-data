"""
sync.py - orchestrátor, který dá dohromady discovery, resolve a stažení
statistik do jedné databáze.

Postup pro danou sezónu:

1. discovery.discover_matches(season) - najde zápasy Sršňů a uloží je
   (nebo aktualizuje) do tabulky matches.
2. Pro zápasy, které ještě nemají fiba_id, zkusí resolve.get_fiba_id -
   pokud zápas ještě nemá přiřazený FIBA feed, v klidu to přeskočí.
3. Pro zápasy, které fiba_id mají, a nemají ještě dnešní snapshot (nebo
   nejsou "finished"), stáhne https://fibalivestats.dcd.shared.geniussports.com/data/<fiba_id>/data.json,
   uloží ho jako nový snapshot a podle stats.is_match_finished()
   aktualizuje status zápasu na finished/live/upcoming.

Poznámka: parsování data/času zápasu a jmen týmů z textu na stránce
rozpisu (summary_text z discovery.py) je jen heuristika - přesný formát
textu na nbl.basketball nebyl možné v této sandboxi ověřit (síť je
zablokovaná organizační politikou). Pokud parsování selže, prostě se to
pole nechá prázdné - nic tím nespadne.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import db
import resolve
import stats
from discovery import USER_AGENT, discover_matches

FIBA_DATA_URL_TEMPLATE = "https://fibalivestats.dcd.shared.geniussports.com/data/{fiba_id}/data.json"

REQUEST_DELAY_SECONDS = 0.8

# Heuristika na datum+čas zápasu v textu okolo odkazu, např. "14.9.2025 17:00".
# Formát nebyl možné ověřit naživo - viz poznámka v README.
_DATE_TIME_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})?\D{0,10}(\d{1,2}):(\d{2})")

# Heuristika na oddělovač mezi domácím a hostujícím týmem ("Tym A - Tym B").
_TEAM_SEPARATOR_RE = re.compile(r"\s(?:-|–|vs\.?)\s")

# Zkratky dnů v týdnu, které se občas objevují na začátku textu ("So 14.9. ...").
_WEEKDAY_PREFIX_RE = re.compile(r"^(?:Po|Út|St|Čt|Pá|So|Ne)\b[.,]?\s*", re.IGNORECASE)

PRAGUE_TZ = ZoneInfo("Europe/Prague")


def parse_kickoff_utc(summary_text: str, default_year: int | None = None) -> str | None:
    """Zkusí z textu vytáhnout datum+čas výkopu a převést ho na UTC ISO string."""
    match = _DATE_TIME_RE.search(summary_text)
    if not match:
        return None

    day, month, year, hour, minute = match.groups()
    year_int = int(year) if year else (default_year or datetime.now().year)
    try:
        local_dt = datetime(year_int, int(month), int(day), int(hour), int(minute), tzinfo=PRAGUE_TZ)
    except ValueError:
        return None
    return local_dt.astimezone(timezone.utc).isoformat()


def parse_teams(summary_text: str) -> tuple[str | None, str | None]:
    """Zkusí z textu vytáhnout jména domácího a hostujícího týmu."""
    text_without_date = _WEEKDAY_PREFIX_RE.sub("", _DATE_TIME_RE.sub(" ", summary_text).strip())
    parts = [p.strip(" .,:-–") for p in _TEAM_SEPARATOR_RE.split(text_without_date) if p.strip(" .,:-–")]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None, None


def fetch_fiba_data(fiba_id: int, session: requests.Session) -> dict:
    """Stáhne aktuální data.json pro daný fiba_id."""
    url = FIBA_DATA_URL_TEMPLATE.format(fiba_id=fiba_id)
    response = session.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def _decide_status(raw_json: dict, date_utc: str | None) -> str:
    """
    Rozhodne stav zápasu podle pravidla ze zadání: perioda 4 + čas 00:00
    (a bez OT) = finished; jinak live/upcoming podle naplánovaného termínu.
    """
    if stats.is_match_finished(raw_json):
        return "finished"

    if date_utc is None:
        # Bez známého termínu radši předpokládáme, že se hraje (máme přece
        # čerstvá data z FIBA feedu, takže zápas evidentně existuje).
        return "live"

    kickoff = datetime.fromisoformat(date_utc)
    now = datetime.now(timezone.utc)
    return "upcoming" if kickoff > now else "live"


def sync_season(season: str | None = None, db_path=db.DB_PATH) -> dict:
    """
    Provede jeden kompletní synchronizační běh pro danou sezónu.

    Vrací malé shrnutí (kolik zápasů objeveno/resolvováno/staženo), ať má
    volající (CLI nebo MCP nástroj sync_schedule) co ukázat uživateli.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    summary = {"discovered": 0, "resolved": 0, "snapshots_saved": 0}

    with db.connect(db_path) as conn:
        discovered = discover_matches(season)
        summary["discovered"] = len(discovered)

        for item in discovered:
            date_utc = parse_kickoff_utc(item.summary_text)
            home_team, away_team = parse_teams(item.summary_text)
            db.upsert_match(
                conn,
                item.nbl_id,
                season=season,
                date_utc=date_utc,
                home_team=home_team,
                away_team=away_team,
            )

        matches = db.list_matches(conn, season=season)

        # Krok 2: dohledat fiba_id u zápasů, které ho ještě nemají.
        for match in matches:
            if match.fiba_id is not None:
                continue
            try:
                fiba_id = resolve.get_fiba_id(match.nbl_id, session)
            except requests.RequestException:
                continue
            time.sleep(REQUEST_DELAY_SECONDS)
            if fiba_id is not None:
                db.upsert_match(conn, match.nbl_id, fiba_id=fiba_id)
                summary["resolved"] += 1

        # Krok 3: stáhnout aktuální data.json tam, kde dává smysl.
        for match in db.list_matches(conn, season=season):
            if match.fiba_id is None:
                continue
            if match.status == "finished" and db.has_snapshot_today(conn, match.nbl_id):
                continue

            try:
                raw_json = fetch_fiba_data(match.fiba_id, session)
            except requests.RequestException:
                continue
            time.sleep(REQUEST_DELAY_SECONDS)

            db.add_snapshot(conn, match.nbl_id, raw_json, clock=raw_json.get("clock"), period=raw_json.get("period"))
            new_status = _decide_status(raw_json, match.date_utc)
            db.upsert_match(conn, match.nbl_id, status=new_status)
            db.mark_synced(conn, match.nbl_id)
            summary["snapshots_saved"] += 1

    return summary


if __name__ == "__main__":
    import sys

    season_arg = sys.argv[1] if len(sys.argv) > 1 else None
    result = sync_season(season_arg)
    print(f"Sezóna: {season_arg or '(aktuální)'}")
    print(f"Objeveno zápasů: {result['discovered']}")
    print(f"Dohledáno nových fiba_id: {result['resolved']}")
    print(f"Uloženo nových snapshotů: {result['snapshots_saved']}")
