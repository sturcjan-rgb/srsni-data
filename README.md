# srsni-data

Datový systém pro basketbalový klub **Sršni Photomate Písek**. Umí:

- najít všechny zápasy klubu v dané sezóně (`discovery.py`),
- dohledat k nim FIBA LiveStats feed (`resolve.py`),
- stáhnout a uložit statistiky do jedné lokální SQLite databáze
  (`db.py`, `sync.py`),
- sledovat právě probíhající zápas naživo (`live.py`),
- a poskytnout to všechno jako nástroje pro Claude Code přes vlastní MCP
  server (`mcp_server.py`).

Je to čistě Python projekt spravovaný přes [uv](https://docs.astral.sh/uv/).
V repozitáři vedle běží ještě samostatný, staticky nastavený Node.js
scraper (`season-scraper.mjs` + GitHub Action `season.yml`) - ten je
nezávislý a tenhle projekt do něj nijak nezasahuje.

## Jak to funguje pod kapotou

1. `discovery.py` stáhne stránku týmu `https://nbl.basketball/tym/srsni-photomate-pisek`
   (funguje i bez JavaScriptu) a najde všechny odkazy na zápasy.
2. `resolve.py` pro každý zápas otevře jeho detail `https://nbl.basketball/zapas/<nbl_id>`
   a najde tam odkaz na FIBA LiveStats webcast, ze kterého vytáhne `fiba_id`.
   U zápasů, které se ještě neodehrály, tenhle odkaz typicky ještě není -
   to není chyba, prostě se to zkusí příště.
3. Ze `fiba_id` se dá přímo stáhnout JSON se statistikami:
   `https://fibalivestats.dcd.shared.geniussports.com/data/<fiba_id>/data.json`
4. Všechno se ukládá do `srsni.db` (SQLite, negituje se) - tabulka
   `matches` (jeden řádek na zápas) a `snapshots` (historie stažených
   JSONů v čase - nikdy se nepřepisují, jen přidávají).
5. `stats.py` umí z toho JSONu spočítat True Shooting %, Effective FG %
   a poměr asistence/ztráty - čistě, bez sítě, otestováno v `test_stats.py`.

## Instalace

Potřebuješ [uv](https://docs.astral.sh/uv/) a Python 3.10+ (uv si ho v
případě potřeby sám dotáhne).

```bash
uv sync
```

## Vyzkoušet na jednom konkrétním zápase

Nejrychlejší way, jak si systém osahat, je ověřit celý řetězec na
jednom známém zápase (`nbl_id=492428`, `fiba_id=2637874` - viz zadání
projektu):

```bash
# 1) dohledání fiba_id ze stránky zápasu
uv run python3 resolve.py 492428

# 2) spuštění testů nad stats.py (bez sítě, na vymyšlených datech)
uv run python3 test_stats.py

# 3) plná synchronizace aktuální sezóny (najde zápasy, dohledá fiba_id,
#    stáhne statistiky tam, kde už existují)
uv run python3 sync.py

# volitelně konkrétní historická sezóna:
uv run python3 sync.py "2024/25"

# 4) sledování dnešního zápasu naživo (nebo konkrétního nbl_id)
uv run python3 live.py
uv run python3 live.py 492428
```

Po `sync.py` se v adresáři objeví `srsni.db` - dá se prohlížet třeba
přes `sqlite3 srsni.db` nebo jakýkoliv SQLite prohlížeč.

### Důležitá poznámka k ověření naživo

Tenhle projekt byl vyvíjený v sandboxu, kde organizační síťová politika
blokovala přístup na `nbl.basketball` i `fibalivestats.com` /
`geniussports.com` - nešlo tedy živě ověřit:

- přesný formát URL parametru pro přepnutí na historickou sezónu
  (`discovery.py` si ho zkouší zjistit sám za běhu - vyzkouší několik
  kandidátních query parametrů a porovná, jestli se výpis zápasů
  skutečně změnil),
- přesný formát textu s datem/týmy na stránce rozpisu (`sync.py` má na
  to heuristiku `parse_kickoff_utc` / `parse_teams`, ale je potřeba ji
  po prvním ostrém spuštění zkontrolovat a případně doladit).

Základní řetězec (`resolve.py` → `fibalivestats.dcd.shared.geniussports.com/data/<id>/data.json`
→ `stats.py`) vychází z faktů ověřených předem naživo, takže by měl
fungovat bez úprav. Při prvním spuštění v prostředí s běžným internetem
stačí zkontrolovat výstup `sync.py` a podle potřeby doladit regulární
výrazy v `sync.py`.

## Připojení do Claude Code

Server běží přes stdio transport, takže se dá zaregistrovat příkazem
`claude mcp add` (spusť z tohohle adresáře, nebo uprav cestu):

```bash
claude mcp add srsni-data -- uv run --directory /cesta/k/repozitari/srsni-data python3 mcp_server.py
```

Po připojení má Claude k dispozici čtyři nástroje:

- `list_matches(season)` - seznam zápasů Sršňů a jejich stav
  (`upcoming` / `live` / `finished`)
- `get_boxscore(match_ref)` - poslední známý stav zápasu po hráčích;
  `match_ref` může být `nbl_id`, `fiba_id`, nebo `"latest"` (poslední
  živý/odehraný zápas)
- `get_advanced_stats(match_ref, metric)` - pokročilá metrika po
  hráčích (`true_shooting_pct`, `effective_fg_pct`,
  `assist_to_turnover_ratio`)
- `sync_schedule(season)` - spustí synchronizaci pro danou sezónu na
  počkání a vrátí shrnutí

Než se dá cokoliv smysluplného vyčíst, je potřeba databázi aspoň jednou
naplnit - buď ručně přes `uv run python3 sync.py`, nebo přímo z Claude
Code zavoláním nástroje `sync_schedule`.

## Poznámka k balíčku `mcp`

Balíček `mcp` na PyPI má aktuálně živou verzi 2.x, která přejmenovala
`FastMCP` na `MCPServer` a mění další API. Tenhle projekt proto v
`pyproject.toml` pinuje `mcp[cli]<2`, aby fungovalo staré, stabilní
`FastMCP` API (`from mcp.server.fastmcp import FastMCP`, `@mcp.tool()`,
`mcp.run(transport="stdio")`), které je použité v `mcp_server.py`.

## Struktura projektu

```
discovery.py     - najde zápasy Sršňů v rozpisu (nbl.basketball)
resolve.py       - dohledá fiba_id ze stránky konkrétního zápasu
db.py            - SQLite databáze (matches, snapshots)
stats.py         - čisté funkce nad JSONem z FIBA LiveStats
test_stats.py    - testy pro stats.py (bez sítě)
sync.py          - orchestrátor: discovery -> resolve -> stažení dat
live.py          - živé sledování probíhajícího zápasu
mcp_server.py    - MCP server nad databází pro Claude Code
```
