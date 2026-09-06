"""
Testy pro stats.py na vymyšlených datech (bez sítě).

Spouští se přes: uv run pytest   (nebo uv run python3 test_stats.py)
"""

from stats import (
    assist_to_turnover_ratio,
    effective_fg_pct,
    is_match_finished,
    parse_boxscore,
    true_shooting_pct,
)

SAMPLE_JSON = {
    "clock": "00:00",
    "period": 4,
    "periodLength": 10,
    "inOT": False,
    "tm": {
        "1": {
            "name": "BK Olomoucko",
            "pl": {
                "101": {
                    "firstName": "Jan",
                    "familyName": "Novak",
                    "sPoints": "20",
                    "sFieldGoalsAttempted": "15",
                    "sFieldGoalsMade": "8",
                    "sTwoPointersAttempted": "10",
                    "sTwoPointersMade": "6",
                    "sThreePointersAttempted": "5",
                    "sThreePointersMade": "2",
                    "sFreeThrowsAttempted": "4",
                    "sFreeThrowsMade": "4",
                    "sReboundsOffensive": "2",
                    "sReboundsDefensive": "3",
                    "sReboundsTotal": "5",
                    "sAssists": "6",
                    "sTurnovers": "2",
                    "sSteals": "1",
                    "sBlocks": "0",
                    "sFoulsPersonal": "3",
                    "sPlusMinusPoints": "5",
                }
            },
        },
        "2": {
            "name": "Srsni Photomate Pisek",
            "pl": {
                "201": {
                    "firstName": "Petr",
                    "familyName": "Svoboda",
                    "sPoints": "0",
                    "sFieldGoalsAttempted": "0",
                    "sFreeThrowsAttempted": "0",
                    "sAssists": "0",
                    "sTurnovers": "0",
                }
            },
        },
    },
}


def test_true_shooting_pct_basic():
    # 20 bodů, 15 FGA, 4 FTA -> 20 / (2*(15+0.44*4)) = 20/33.52
    result = true_shooting_pct(20, 15, 4)
    assert result is not None
    assert round(result, 4) == round(20 / (2 * (15 + 0.44 * 4)), 4)


def test_true_shooting_pct_no_attempts_is_none():
    assert true_shooting_pct(0, 0, 0) is None


def test_effective_fg_pct():
    # 8 made, 2 z toho trojky, 15 pokusů -> (8 + 0.5*2)/15
    result = effective_fg_pct(8, 2, 15)
    assert round(result, 4) == round(9 / 15, 4)


def test_effective_fg_pct_no_attempts_is_none():
    assert effective_fg_pct(0, 0, 0) is None


def test_assist_to_turnover_ratio():
    assert assist_to_turnover_ratio(6, 2) == 3.0
    assert assist_to_turnover_ratio(6, 0) is None


def test_parse_boxscore_teams_and_players():
    box = parse_boxscore(SAMPLE_JSON)
    assert box.period == 4
    assert box.clock == "00:00"
    assert len(box.teams) == 2

    srsni = box.find_team("Srsni")
    assert srsni is not None
    assert srsni.name == "Srsni Photomate Pisek"

    olomoucko = box.find_team("Olomoucko")
    assert olomoucko is not None
    player = olomoucko.players[0]
    assert player.full_name == "Jan Novak"
    assert player.points == 20
    assert player.true_shooting_pct is not None
    assert player.effective_fg_pct is not None
    assert player.assist_to_turnover_ratio == 3.0


def test_parse_boxscore_player_without_attempts_has_none_percentages():
    box = parse_boxscore(SAMPLE_JSON)
    srsni = box.find_team("Srsni")
    player = srsni.players[0]
    assert player.true_shooting_pct is None
    assert player.effective_fg_pct is None
    assert player.assist_to_turnover_ratio is None


def test_is_match_finished_true_at_end_of_regulation():
    assert is_match_finished(SAMPLE_JSON) is True


def test_is_match_finished_false_mid_game():
    mid_game = {**SAMPLE_JSON, "period": 2, "clock": "05:30"}
    assert is_match_finished(mid_game) is False


def test_is_match_finished_false_before_period_4_ends():
    almost = {**SAMPLE_JSON, "period": 3, "clock": "00:00"}
    assert is_match_finished(almost) is False


if __name__ == "__main__":
    import sys

    failures = 0
    for test_name, test_fn in list(globals().items()):
        if test_name.startswith("test_") and callable(test_fn):
            try:
                test_fn()
                print(f"OK   {test_name}")
            except AssertionError:
                failures += 1
                print(f"FAIL {test_name}")
    sys.exit(1 if failures else 0)
