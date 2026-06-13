"""Hämta kyrkoårets helgdagar från svk-API:t och skriv seed_data/kyrkoaret.yaml.

Bygger en tre-nivåers occasion-tagghierarki:

    Kyrkoåret (rot)
      kyrkoårstid   (churchYearPart, t.ex. Advent, Jul, Fasta)
        helgdag     (feast, t.ex. Första söndagen i advent)

Helgdagsnamn tas verbatim från API:t. otherName (latinska namn m.m.) blir
alias så fritextsök hittar t.ex. "Palmarum". Kör om vid behov - filen är
genererad referensdata och checkas in.

Användning:
    uv run python scripts/fetch_kyrkoaret.py [--year 2025]
"""

import argparse
import sys
from pathlib import Path

import httpx
import yaml

API_URL = "http://svk-api.pettersson-vik.se/"
OUT_PATH = Path("seed_data/kyrkoaret.yaml")
ROOT_NAME = "Kyrkoåret"
ROOT_DESCRIPTION = "Tider och söndagar i kyrkoåret"


def _fetch(year: int) -> list[dict]:
    resp = httpx.get(API_URL, params={"year": year}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _build_rows(feasts: list[dict]) -> list[dict]:
    rows: list[dict] = [
        {
            "name": ROOT_NAME,
            "kind": "occasion",
            "description": ROOT_DESCRIPTION,
            "sort_order": 0,
        }
    ]

    # Gruppera helgdagar per kyrkoårstid.
    parts: dict[int, dict] = {}
    for f in feasts:
        part = f.get("churchYearPart") or {}
        pid = part.get("id")
        pname = part.get("name")
        if pid is None or not pname:
            continue
        if pid not in parts:
            parts[pid] = {"name": pname, "description": part.get("description"), "feasts": []}
        parts[pid]["feasts"].append(f)

    # Ordna kronologiskt efter när tiden infaller. ISO-datumsträngar
    # sorterar lexikografiskt = kronologiskt, så ingen datetime-parsing behövs.
    # "Övriga helgdagar" är utspridda över hela året och pinnas därför sist
    # i stället för att hamna mitt i flödet vid sin första helgdag.
    def feast_date(f: dict) -> str:
        return f.get("startDate") or ""

    def season_sort_key(pid: int) -> tuple:
        is_other = parts[pid]["name"] == "Övriga helgdagar"
        return (1 if is_other else 0, min(feast_date(f) for f in parts[pid]["feasts"]))

    season_keys = sorted(parts, key=season_sort_key)

    # Globalt över alla kyrkoårstider: alias-namn (TagAlias.name) måste vara unika,
    # så en otherName som råkar återkomma i en annan tid får inte dupliceras.
    seen_alias: set[str] = set()

    for rank, pid in enumerate(season_keys, start=1):
        part = parts[pid]
        season_row: dict = {"name": part["name"], "kind": "occasion", "parent": ROOT_NAME, "sort_order": rank}
        if part["description"]:
            season_row["description"] = part["description"].strip()
        rows.append(season_row)

        season_feasts = sorted(part["feasts"], key=feast_date)
        # sort_order = rank*100 + index: helgdagar klustrar under sin kyrkoårstid
        # även i platta listor (t.ex. occasion-pickern), i kronologisk ordning.
        for i, f in enumerate(season_feasts, start=1):
            name = (f.get("feastName") or "").strip()
            if not name:
                continue
            feast_row: dict = {
                "name": name,
                "kind": "occasion",
                "parent": part["name"],
                "sort_order": rank * 100 + i,
            }
            other = (f.get("otherName") or "").strip()
            # Skippa tomma och dubbletter; alias-namn måste vara unika.
            if other and other != name and other not in seen_alias:
                feast_row["aliases"] = [other]
                seen_alias.add(other)
            rows.append(feast_row)

    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    args = ap.parse_args()

    feasts = _fetch(args.year)
    rows = _build_rows(feasts)

    header = (
        "# Kyrkoårets helgdagar - GENERERAD av scripts/fetch_kyrkoaret.py\n"
        f"# Källa: {API_URL} (year={args.year})\n"
        "# Tre nivåer: Kyrkoåret > kyrkoårstid > helgdag. Alla kind: occasion.\n"
        "# Redigera inte för hand - kör om scriptet. Justeringar görs i admin.\n"
    )
    body = yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, default_flow_style=False)
    OUT_PATH.write_text(header + body, encoding="utf-8")

    n_seasons = sum(1 for r in rows if r.get("parent") == ROOT_NAME)
    n_feasts = sum(1 for r in rows if r.get("parent") not in (None, ROOT_NAME))
    print(f"Skrev {OUT_PATH}: {n_seasons} kyrkoårstider, {n_feasts} helgdagar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
