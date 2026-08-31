#!/usr/bin/env python3
"""Build canonical season JSON from parsed fixture rows.
Shared derivation logic (venue, quarters, links, ISO datetime) — the live
Node scraper reuses the same field mapping when parsing the NBL team page DOM.
"""
import json, re, sys
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")
TEAM = "Sršni Photomate Písek"
ALIASES = ("srsni", "sršni", "sokol", "pisek", "písek", "photomate")

MONTHS_OK = True

def norm(s):
    return s.lower()

def is_us(name):
    n = norm(name)
    return any(a in n for a in ALIASES)

def parse_date(d, t):
    # d like "28. 9. 2025", t like "17:00"
    day, month, year = [int(x) for x in re.findall(r"\d+", d)]
    hh, mm = [int(x) for x in t.split(":")]
    dt = datetime(year, month, day, hh, mm, tzinfo=TZ)
    return dt

def split_quarters(quarters_str, final_home, final_away):
    nums = [int(x) for x in quarters_str.split()] if quarters_str.strip() else []
    pairs = [(nums[i], nums[i+1]) for i in range(0, len(nums), 2)]
    checkpoints = pairs + [(final_home, final_away)]  # cumulative incl. final
    periods = []
    prev_h, prev_a = 0, 0
    for (ch, ca) in checkpoints:
        periods.append({"home": ch - prev_h, "away": ca - prev_a})
        prev_h, prev_a = ch, ca
    reg = periods[:4]
    ot = periods[4:]
    return {
        "byPeriod": periods,
        "regulation": reg,
        "overtimes": ot,
        "otCount": len(ot),
        "cumulative": [{"home": h, "away": a} for (h, a) in checkpoints],
    }

def build_fixture(row):
    (rnd, date, time, home, away, hs, as_, quarters, nbl, fiba, phase) = row
    played = hs.strip() != "" and as_.strip() != ""
    hs = int(hs) if hs.strip() else None
    as_ = int(as_) if as_.strip() else None
    fiba = fiba.strip() or None
    dt = parse_date(date, time)
    venue = "home" if is_us(home) else "away"
    opponent = away if venue == "home" else home
    us = (hs if venue == "home" else as_) if played else None
    them = (as_ if venue == "home" else hs) if played else None
    m = re.match(r"(\d+)", rnd) if rnd.strip() else None
    round_num = int(m.group(1)) if m else None
    fixture = {
        "nblMatchId": int(nbl),
        "fibaMatchId": int(fiba) if fiba else None,
        "round": rnd or None,
        "roundNum": round_num,
        "phase": phase or None,
        "date": dt.strftime("%Y-%m-%d"),
        "time": time,
        "tz": "Europe/Prague",
        "datetime": dt.isoformat(),
        "home": home,
        "away": away,
        "opponent": opponent,
        "venue": venue,
        "status": "final" if played else "scheduled",
        "score": {
            "home": hs, "away": as_,
            "us": us, "them": them,
            "result": ("W" if us > them else "L") if played else None,
            "margin": (us - them) if played else None,
        },
        "quarters": split_quarters(quarters, hs, as_) if played else None,
        "links": {
            "nbl": f"https://nbl.basketball/zapas/{nbl}",
            "livestats": f"https://www.fibalivestats.com/webcast/CBFFE/{fiba}/" if fiba else None,
            "fibaData": f"https://fibalivestats.dcd.shared.geniussports.com/data/{fiba}/data.json" if fiba else None,
        },
    }
    return fixture

def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 11:
                print(f"WARN skipping malformed row ({len(parts)} cols): {line[:60]}", file=sys.stderr)
                continue
            rows.append(parts)
    return rows

def build_season(season, source, rows):
    fixtures = [build_fixture(r) for r in rows]
    fixtures.sort(key=lambda x: x["datetime"])
    return {
        "season": season,
        "team": {"name": TEAM, "nblId": 10110, "nblSlug": "srsni-photomate-pisek"},
        "generatedAt": datetime.now(TZ).isoformat(),
        "source": source,
        "counts": {
            "total": len(fixtures),
            "home": sum(1 for f in fixtures if f["venue"] == "home"),
            "away": sum(1 for f in fixtures if f["venue"] == "away"),
            "final": sum(1 for f in fixtures if f["status"] == "final"),
            "scheduled": sum(1 for f in fixtures if f["status"] == "scheduled"),
        },
        "fixtures": fixtures,
    }

if __name__ == "__main__":
    rows = load_rows("/home/claude/srsni-data/fixtures-2025-26.raw.tsv")
    season = build_season(
        "2025/26",
        "https://nbl.basketball/tym/srsni-photomate-pisek",
        rows,
    )
    with open("/home/claude/srsni-data/season-2025-26.json", "w", encoding="utf-8") as f:
        json.dump(season, f, ensure_ascii=False, indent=2)
    print(json.dumps(season["counts"], ensure_ascii=False))
    # quick self-check: W/L tally
    wl = {}
    for fx in season["fixtures"]:
        r = fx["score"]["result"]
        wl[r] = wl.get(r, 0) + 1
    print("W/L:", wl)
