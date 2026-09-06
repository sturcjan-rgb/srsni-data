"""
Resolve: pro daný nbl_id najde odpovídající fiba_id.

Stránka detailu zápasu (https://nbl.basketball/zapas/<nbl_id>) je taky
staticky vykreslená a u odehraných (příp. právě probíhajících) zápasů
obsahuje odkaz na FIBA LiveStats webcast ve tvaru:

    https://www.fibalivestats.com/webcast/<KOD>/<fiba_id>/

<fiba_id> je přesně to číslo, které se pak používá v přímém JSON
endpointu https://fibalivestats.dcd.shared.geniussports.com/data/<fiba_id>/data.json
(viz stats.py / sync.py).

U budoucích/nadcházejících zápasů tenhle odkaz ještě nemusí existovat -
FIBA feed se přiřazuje blíž k termínu zápasu. To není chyba, jen zatím
nemáme co stahovat - proto get_fiba_id vrací None místo vyhazování chyby.
"""

from __future__ import annotations

import re
import time

import requests

from discovery import USER_AGENT

FIBA_WEBCAST_RE = re.compile(
    r"https://www\.fibalivestats\.com/webcast/[^/\s\"']+/(\d+)/?"
)

REQUEST_DELAY_SECONDS = 0.8


def get_fiba_id(nbl_id: int, session: requests.Session | None = None) -> int | None:
    """
    Stáhne stránku zápasu na nbl.basketball a pokusí se z ní vytáhnout fiba_id.

    Vrací None, pokud odkaz na FIBA LiveStats na stránce ještě není
    (typicky u zápasu, který se ještě neodehrál).
    """
    own_session = session is None
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT

    url = f"https://nbl.basketball/zapas/{nbl_id}"
    response = session.get(url, timeout=15)
    response.raise_for_status()

    if own_session:
        # Pauzu dáváme jen když si session vytváříme sami - při volání
        # ve smyčce (viz sync.py) si o pauzy mezi requesty stará volající.
        time.sleep(REQUEST_DELAY_SECONDS)

    match = FIBA_WEBCAST_RE.search(response.text)
    if not match:
        return None
    return int(match.group(1))


if __name__ == "__main__":
    import sys

    nbl_id_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 492428
    fiba_id = get_fiba_id(nbl_id_arg)
    print(f"nbl_id={nbl_id_arg} -> fiba_id={fiba_id}")
