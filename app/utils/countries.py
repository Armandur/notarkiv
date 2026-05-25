"""Hjälpare för land: ISO-koder → svenskt namn + emoji-flagga.

Använder babel/CLDR för översättningarna - ger alla ~250 ISO 3166-1
alpha-2-koder på svenska utan att hand-underhålla en lista.
"""

from functools import lru_cache

from babel import Locale

# Codes som CLDR har men som inte är riktiga ISO 3166-1-länder
# (transnationella regioner mm). Filtrera bort så valbara länder är
# bara riktiga territorier.
_NON_COUNTRY_CODES = {"EU", "EZ", "UN", "QO", "XA", "XB"}


@lru_cache(maxsize=1)
def _sv_locale() -> Locale:
    return Locale.parse("sv_SE")


def country_flag_emoji(code: str | None) -> str:
    """Konvertera ISO 3166-1 alpha-2 till emoji-flagga via regional indicator symbols."""
    if not code or len(code) != 2 or not code.isalpha():
        return ""
    code = code.upper()
    return chr(0x1F1E6 + ord(code[0]) - ord("A")) + chr(
        0x1F1E6 + ord(code[1]) - ord("A")
    )


def country_name_sv(code: str | None) -> str:
    """Svenskt landsnamn för en ISO-kod (eller koden själv om okänt)."""
    if not code:
        return ""
    code = code.upper()
    name = _sv_locale().territories.get(code)
    return name or code


def country_display(code: str | None) -> str:
    """Format: '🇩🇪 Tyskland'. Tom sträng om kod saknas."""
    if not code:
        return ""
    flag = country_flag_emoji(code)
    name = country_name_sv(code)
    return f"{flag} {name}".strip()


@lru_cache(maxsize=1)
def all_countries() -> list[dict]:
    """Alla ISO 3166-1 alpha-2-koder sorterade på svenskt namn."""
    sv = _sv_locale()
    rows = []
    for code, name in sv.territories.items():
        if len(code) != 2 or not code.isalpha():
            continue
        if code in _NON_COUNTRY_CODES:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "display": f"{country_flag_emoji(code)} {name}",
            }
        )
    rows.sort(key=lambda r: r["name"])
    return rows
