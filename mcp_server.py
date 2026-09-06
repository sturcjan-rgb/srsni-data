"""
MCP server nad databází srsni.db.

Na rozdíl od "obyčejného" MCP serveru, který by třeba jen zabalil jedno
URL, tenhle server čte z lokální SQLite databáze (db.py), kterou plní
sync.py a live.py. Díky tomu funguje i offline a vrací i historická data.

Použité API je z balíčku "mcp[cli]<2" (stará, stabilní FastMCP API) -
viz poznámka v README, proč zrovna tahle verze.

Spuštění (stdio transport, jak to očekává Claude Code):

    uv run python3 mcp_server.py

Registrace do Claude Code:

    claude mcp add srsni-data -- uv run --directory /cesta/k/projektu python3 mcp_server.py
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import db
import stats
from sync import sync_season

mcp = FastMCP("srsni-data")

# Metriky, které umí get_advanced_stats spočítat - jde o názvy vlastností
# na stats.PlayerStats (viz stats.py).
ADVANCED_METRICS = ("true_shooting_pct", "effective_fg_pct", "assist_to_turnover_ratio")


def _match_to_dict(match: db.Match) -> dict:
    return {
        "nbl_id": match.nbl_id,
        "fiba_id": match.fiba_id,
        "season": match.season,
        "date_utc": match.date_utc,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "status": match.status,
        "last_synced_at": match.last_synced_at,
    }


def _player_to_dict(player: stats.PlayerStats) -> dict:
    return {
        "player_id": player.player_id,
        "name": player.full_name,
        "points": player.points,
        "field_goals_made": player.field_goals_made,
        "field_goals_attempted": player.field_goals_attempted,
        "three_pt_made": player.three_pt_made,
        "three_pt_attempted": player.three_pt_attempted,
        "free_throws_made": player.free_throws_made,
        "free_throws_attempted": player.free_throws_attempted,
        "rebounds_total": player.rebounds_total,
        "assists": player.assists,
        "turnovers": player.turnovers,
        "steals": player.steals,
        "blocks": player.blocks,
        "fouls_personal": player.fouls_personal,
        "plus_minus": player.plus_minus,
        "true_shooting_pct": player.true_shooting_pct,
        "effective_fg_pct": player.effective_fg_pct,
        "assist_to_turnover_ratio": player.assist_to_turnover_ratio,
    }


def _resolve_match(conn, match_ref: str) -> db.Match | None:
    """
    match_ref může být:
    - "latest" - poslední živý/odehraný zápas
    - nbl_id (číslo z nbl.basketball)
    - fiba_id (číslo z FIBA LiveStats)
    """
    if match_ref == "latest":
        return db.get_latest_match(conn)

    try:
        ref_id = int(match_ref)
    except (TypeError, ValueError):
        return None

    match = db.get_match(conn, ref_id)
    if match is not None:
        return match
    return db.get_match_by_fiba_id(conn, ref_id)


@mcp.tool()
def list_matches(season: str | None = None) -> list[dict]:
    """
    Vrátí zápasy Sršňů a jejich stav (upcoming/live/finished).

    season: např. "2025/26". Když se vynechá, vrátí zápasy ze všech sezón,
    které máme v databázi.
    """
    with db.connect() as conn:
        matches = db.list_matches(conn, season=season)
    return [_match_to_dict(m) for m in matches]


@mcp.tool()
def get_boxscore(match_ref: str) -> dict:
    """
    Vrátí poslední známý stav zápasu (skóre po týmech a hráčích).

    match_ref: nbl_id, fiba_id, nebo "latest" pro poslední živý/odehraný
    zápas Sršňů.
    """
    with db.connect() as conn:
        match = _resolve_match(conn, match_ref)
        if match is None:
            return {"error": f"Zápas '{match_ref}' nebyl v databázi nalezen."}
        raw_json = db.get_latest_snapshot(conn, match.nbl_id)

    if raw_json is None:
        return {
            "error": "Pro tenhle zápas zatím není žádný uložený snapshot.",
            "hint": "Zkus nejdřív zavolat sync_schedule pro příslušnou sezónu.",
            "match": _match_to_dict(match),
        }

    box = stats.parse_boxscore(raw_json)
    return {
        "match": _match_to_dict(match),
        "clock": box.clock,
        "period": box.period,
        "period_length": box.period_length,
        "in_ot": box.in_ot,
        "teams": [
            {
                "team_id": team.team_id,
                "name": team.name,
                "players": [_player_to_dict(p) for p in team.players],
            }
            for team in box.teams
        ],
    }


@mcp.tool()
def get_advanced_stats(match_ref: str, metric: str = "true_shooting_pct") -> dict:
    """
    Vrátí hodnotu pokročilé metriky pro každého hráče v zápase.

    match_ref: nbl_id, fiba_id, nebo "latest".
    metric: jedna z ADVANCED_METRICS - "true_shooting_pct", "effective_fg_pct"
    nebo "assist_to_turnover_ratio".
    """
    if metric not in ADVANCED_METRICS:
        return {"error": f"Neznámá metrika '{metric}'. Podporované: {', '.join(ADVANCED_METRICS)}."}

    with db.connect() as conn:
        match = _resolve_match(conn, match_ref)
        if match is None:
            return {"error": f"Zápas '{match_ref}' nebyl v databázi nalezen."}
        raw_json = db.get_latest_snapshot(conn, match.nbl_id)

    if raw_json is None:
        return {"error": "Pro tenhle zápas zatím není žádný uložený snapshot."}

    box = stats.parse_boxscore(raw_json)
    return {
        "match": _match_to_dict(match),
        "metric": metric,
        "teams": [
            {
                "name": team.name,
                "players": [
                    {"name": player.full_name, "value": getattr(player, metric)}
                    for player in team.players
                ],
            }
            for team in box.teams
        ],
    }


@mcp.tool()
def sync_schedule(season: str | None = None) -> dict:
    """
    Spustí synchronizaci (discovery + resolve + stažení statistik) pro
    danou sezónu a počká na dokončení. Vrací shrnutí, co se stáhlo.

    season: např. "2025/26". Když se vynechá, použije se aktuální sezóna.
    """
    return sync_season(season)


if __name__ == "__main__":
    mcp.run(transport="stdio")
