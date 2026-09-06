"""
Discovery: najde všechny zápasy Sršňů Photomate Písek v dané sezóně.

Zdroj dat: statická (bez JavaScriptu) stránka týmu na nbl.basketball,
viz https://nbl.basketball/tym/srsni-photomate-pisek - obsahuje odkazy
tvaru /zapas/<nbl_id> na jednotlivé zápasy.

Poznámka k sezónám: přesný formát URL parametru pro přepnutí na historickou
sezónu (2024/25, 2023/24, ...) nebyl předem ověřený. Místo natvrdo
zadaného hádání to tahle modul zjišťuje sám za běhu - vyzkouší několik
běžných variant query parametru a porovná, jestli se výpis zápasů v HTML
skutečně změnil oproti výchozí (aktuální) sezóně. Pokud narazí na
fungující variantu, použije ji; jinak spadne zpátky na výchozí sezónu.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

TEAM_URL = "https://nbl.basketball/tym/srsni-photomate-pisek"

# Slušné chování vůči cizímu webu: představíme se a mezi requesty počkáme.
USER_AGENT = "srsni-data/0.1 (+kontakt: klub Srsni Photomate Pisek; osobni projekt pro vlastni statistiky)"
REQUEST_DELAY_SECONDS = 0.8

# Kandidátní formáty query parametru pro přepnutí sezóny - vyzkouší se
# postupně, dokud jeden z nich nezmění výsledný seznam zápasů.
SEASON_PARAM_CANDIDATES = ["season", "sezona", "sezóna", "rocnik", "year", "y"]

# Regulérní výraz na odkazy na detail zápasu, např. href="/zapas/544546"
MATCH_LINK_RE = re.compile(r"^/zapas/(\d+)$")


@dataclass
class DiscoveredMatch:
    """Jeden zápas nalezený v rozpisu - zatím jen to, co je vidět z HTML."""

    nbl_id: int
    url: str
    summary_text: str  # okolní text odkazu (datum, domácí/hosté) pro čitelnost


def _get(session: requests.Session, url: str) -> str:
    """Stáhne stránku a vrátí HTML. Mezi voláními počká, ať web nezatěžujeme."""
    response = session.get(url, timeout=15)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return response.text


def _extract_matches(html: str) -> list[DiscoveredMatch]:
    """Z HTML stránky týmu vytáhne všechny odkazy na zápasy."""
    soup = BeautifulSoup(html, "html.parser")
    matches: list[DiscoveredMatch] = []
    seen_ids: set[int] = set()

    for link in soup.find_all("a", href=True):
        match = MATCH_LINK_RE.match(link["href"])
        if not match:
            continue
        nbl_id = int(match.group(1))
        if nbl_id in seen_ids:
            continue
        seen_ids.add(nbl_id)

        # Okolní text (řádek s datem a týmy) bereme z rodičovského elementu,
        # protože samotný <a> často obaluje jen část informací.
        container = link.parent
        summary_text = container.get_text(" ", strip=True) if container else link.get_text(" ", strip=True)

        matches.append(
            DiscoveredMatch(
                nbl_id=nbl_id,
                url=f"https://nbl.basketball/zapas/{nbl_id}",
                summary_text=summary_text,
            )
        )

    return matches


def _find_season_query(session: requests.Session, season: str, baseline_ids: set[int]) -> str | None:
    """
    Experimentálně zjistí, jaký query parametr přepne rozpis na danou sezónu.

    Zkusí kandidátní parametry ze SEASON_PARAM_CANDIDATES v kombinaci s
    různým formátem hodnoty (např. "2024/25" i "2024-25") a porovná
    výsledné ID zápasů s výchozí (aktuální) sezónou. Pokud se seznam
    zápasů liší a není prázdný, považuje to za nalezenou fungující variantu.
    """
    value_variants = [season, season.replace("/", "-"), season.split("/")[0]]

    for param_name in SEASON_PARAM_CANDIDATES:
        for value in value_variants:
            query = urlencode({param_name: value})
            url = f"{TEAM_URL}?{query}"
            try:
                html = _get(session, url)
            except requests.RequestException:
                continue
            candidate_ids = {m.nbl_id for m in _extract_matches(html)}
            if candidate_ids and candidate_ids != baseline_ids:
                return query

    return None


def discover_matches(season: str | None = None) -> list[DiscoveredMatch]:
    """
    Vrátí seznam zápasů Sršňů pro danou sezónu.

    season: řetězec jako "2024/25", nebo None pro aktuální (výchozí) sezónu.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    baseline_html = _get(session, TEAM_URL)
    baseline_matches = _extract_matches(baseline_html)

    if season is None:
        return baseline_matches

    baseline_ids = {m.nbl_id for m in baseline_matches}
    query = _find_season_query(session, season, baseline_ids)
    if query is None:
        # Nepodařilo se najít funkční parametr - vrátíme aspoň aktuální
        # sezónu, ať volající nedostane prázdno / chybu.
        return baseline_matches

    season_html = _get(session, f"{TEAM_URL}?{query}")
    return _extract_matches(season_html)


if __name__ == "__main__":
    for m in discover_matches():
        print(m.nbl_id, "-", m.summary_text)
