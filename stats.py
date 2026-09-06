"""
Čisté funkce nad JSONem z FIBA LiveStats (data.json).

Žádná síťová komunikace - jen parsování slovníku, který už máme stažený
(ať už čerstvě, nebo jako snapshot z databáze), a výpočet odvozených
statistik. Díky tomu je to snadné otestovat na vymyšlených datech.

Připomenutí schématu (ověřeno na reálném zápase):
- nejvyšší úroveň: "tm" (teams), "clock", "period", "periodLength", "inOT"
- "tm" je OBJEKT klíčovaný podle team id ("1", "2", ...), ne pole
- každý tým má "name" a "pl" (players) - taky objekt klíčovaný podle
  player id, ne pole
- hráčská pole mají prefix "s", jméno je ve "firstName"/"familyName"

Pozor na "sFieldGoalsAttempted" - to je 2P + 3P dohromady, ne jen 2 body.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def true_shooting_pct(points: float, fga: float, fta: float) -> float | None:
    """
    True Shooting % - efektivita střelby, která započítává i trestné hody
    a rozlišuje hodnotu 3bodových pokusů (přes celkové body, ne jen FG%).

    TS% = body / (2 * (FGA + 0.44 * FTA))

    Vrací None, když hráč neměl žádný pokus (aby nedošlo k dělení nulou).
    """
    denominator = 2 * (fga + 0.44 * fta)
    if denominator == 0:
        return None
    return points / denominator


def effective_fg_pct(fgm: float, three_pm: float, fga: float) -> float | None:
    """
    Effective FG% - jako FG%, ale trojka se počítá jako 1.5x hodnotnější
    trefa než dvojka (protože dá o 50 % víc bodů za stejný pokus).

    eFG% = (FGM + 0.5 * 3PM) / FGA
    """
    if fga == 0:
        return None
    return (fgm + 0.5 * three_pm) / fga


def assist_to_turnover_ratio(assists: float, turnovers: float) -> float | None:
    """Poměr asistence/ztráty - čím vyšší, tím "čistší" rozehrávka."""
    if turnovers == 0:
        return None
    return assists / turnovers


@dataclass
class PlayerStats:
    """Statistiky jednoho hráče v jednom zápase (nebo v aktuálním snapshotu)."""

    player_id: str
    first_name: str
    family_name: str
    points: float
    field_goals_attempted: float
    field_goals_made: float
    two_pt_attempted: float
    two_pt_made: float
    three_pt_attempted: float
    three_pt_made: float
    free_throws_attempted: float
    free_throws_made: float
    rebounds_offensive: float
    rebounds_defensive: float
    rebounds_total: float
    assists: float
    turnovers: float
    steals: float
    blocks: float
    fouls_personal: float
    plus_minus: float

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.family_name}".strip()

    @property
    def true_shooting_pct(self) -> float | None:
        return true_shooting_pct(self.points, self.field_goals_attempted, self.free_throws_attempted)

    @property
    def effective_fg_pct(self) -> float | None:
        return effective_fg_pct(self.field_goals_made, self.three_pt_made, self.field_goals_attempted)

    @property
    def assist_to_turnover_ratio(self) -> float | None:
        return assist_to_turnover_ratio(self.assists, self.turnovers)


@dataclass
class TeamStats:
    """Statistiky jednoho týmu - jméno a seznam hráčů."""

    team_id: str
    name: str
    players: list[PlayerStats] = field(default_factory=list)


@dataclass
class Boxscore:
    """Kompletní rozbor jednoho snapshotu zápasu (oba týmy + stav utkání)."""

    clock: str | None
    period: int | None
    period_length: int | None
    in_ot: bool
    teams: list[TeamStats] = field(default_factory=list)

    def find_team(self, name_substring: str) -> TeamStats | None:
        """Najde tým podle části jména (case-insensitive) - pohodlné pro Sršně."""
        needle = name_substring.lower()
        for team in self.teams:
            if needle in team.name.lower():
                return team
        return None


def _to_number(value, default=0):
    """Bezpečně převede hodnotu z JSONu na číslo - FIBA feed občas posílá stringy."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_player(player_id: str, raw: dict) -> PlayerStats:
    """Vytáhne statistiky jednoho hráče ze záznamu v tm.<id>.pl.<player_id>."""
    return PlayerStats(
        player_id=player_id,
        first_name=raw.get("firstName", ""),
        family_name=raw.get("familyName", ""),
        points=_to_number(raw.get("sPoints")),
        field_goals_attempted=_to_number(raw.get("sFieldGoalsAttempted")),
        field_goals_made=_to_number(raw.get("sFieldGoalsMade")),
        two_pt_attempted=_to_number(raw.get("sTwoPointersAttempted")),
        two_pt_made=_to_number(raw.get("sTwoPointersMade")),
        three_pt_attempted=_to_number(raw.get("sThreePointersAttempted")),
        three_pt_made=_to_number(raw.get("sThreePointersMade")),
        free_throws_attempted=_to_number(raw.get("sFreeThrowsAttempted")),
        free_throws_made=_to_number(raw.get("sFreeThrowsMade")),
        rebounds_offensive=_to_number(raw.get("sReboundsOffensive")),
        rebounds_defensive=_to_number(raw.get("sReboundsDefensive")),
        rebounds_total=_to_number(raw.get("sReboundsTotal")),
        assists=_to_number(raw.get("sAssists")),
        turnovers=_to_number(raw.get("sTurnovers")),
        steals=_to_number(raw.get("sSteals")),
        blocks=_to_number(raw.get("sBlocks")),
        fouls_personal=_to_number(raw.get("sFoulsPersonal")),
        plus_minus=_to_number(raw.get("sPlusMinusPoints")),
    )


def parse_team(team_id: str, raw: dict) -> TeamStats:
    """Vytáhne tým i s hráči ze záznamu v tm.<team_id>."""
    players_raw: dict = raw.get("pl", {})
    players = [parse_player(player_id, player_raw) for player_id, player_raw in players_raw.items()]
    return TeamStats(team_id=team_id, name=raw.get("name", ""), players=players)


def parse_boxscore(raw_json: dict) -> Boxscore:
    """Naparsuje celý data.json (nebo snapshot z DB) do přehledné struktury."""
    teams_raw: dict = raw_json.get("tm", {})
    teams = [parse_team(team_id, team_raw) for team_id, team_raw in teams_raw.items()]
    return Boxscore(
        clock=raw_json.get("clock"),
        period=raw_json.get("period"),
        period_length=raw_json.get("periodLength"),
        in_ot=bool(raw_json.get("inOT")),
        teams=teams,
    )


def is_match_finished(raw_json: dict) -> bool:
    """
    Zápas je (podle stavu hodin) hotový, když doběhla 4. perioda na 00:00
    a nejsme v prodloužení. Pokud "inOT" říká, že jsme v prodloužení,
    bereme konec podle toho, že se hodiny prodloužení taky zastavily na 00:00
    (perioda pak bude 5 a víc).
    """
    period = raw_json.get("period")
    clock = raw_json.get("clock")
    in_ot = bool(raw_json.get("inOT"))

    if period is None or clock is None:
        return False

    clock_is_zero = clock in ("00:00", "0:00", "00:00.0", "00:00.00")
    if not clock_is_zero:
        return False

    if in_ot:
        # V prodloužení: konec poznáme podle toho, že hodiny doběhly a
        # zápas dál "neběží" (o to se stará volající - my jen řekneme,
        # že tahle perioda skončila).
        return True

    return period >= 4
