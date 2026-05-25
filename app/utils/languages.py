"""Hjälpare för språk: ISO 639-1-koder → svenskt språknamn + emoji-flagga.

Använder babel/CLDR för översättningar och en kuraterad språk-till-landkod-
mappning för flaggor (en flagga är inte 1-till-1 med ett språk, så detta
är best-effort utifrån primärt talarland).
"""

from functools import lru_cache

from babel import Locale

from app.utils.countries import country_flag_emoji

# Språk → primärland för emoji-flagga. Best-effort - många språk har flera
# stora talargrupper och valet är subjektivt. Codes utan post i denna dict
# får ingen flagga.
_LANG_TO_COUNTRY: dict[str, str] = {
    "sv": "SE", "en": "GB", "de": "DE", "fr": "FR", "es": "ES", "it": "IT",
    "no": "NO", "nb": "NO", "nn": "NO", "da": "DK", "fi": "FI", "is": "IS",
    "nl": "NL", "pt": "PT", "ru": "RU", "pl": "PL", "cs": "CZ", "sk": "SK",
    "sl": "SI", "hr": "HR", "sr": "RS", "bg": "BG", "ro": "RO", "hu": "HU",
    "el": "GR", "tr": "TR", "uk": "UA", "be": "BY", "et": "EE", "lv": "LV",
    "lt": "LT", "ga": "IE", "mt": "MT", "sq": "AL", "mk": "MK", "bs": "BA",
    "he": "IL", "ar": "SA", "fa": "IR", "hi": "IN", "bn": "BD", "ur": "PK",
    "ja": "JP", "ko": "KR", "zh": "CN", "vi": "VN", "th": "TH", "id": "ID",
    "ms": "MY", "tl": "PH", "sw": "TZ", "af": "ZA", "am": "ET",
    "la": "VA",  # latin → Vatikan
    "ca": "AD",  # katalanska → Andorra
    "eu": "ES",  # baskiska → Spanien
    "gl": "ES",  # galiciska → Spanien
    "cy": "GB",  # walesiska → Storbritannien
    "gd": "GB",  # skotsk gaeliska
    "fo": "FO",  # färöiska
    "kl": "GL",  # grönländska
    "se": "NO",  # nordsamiska
}


@lru_cache(maxsize=1)
def _sv_locale() -> Locale:
    return Locale.parse("sv_SE")


def language_flag_emoji(code: str | None) -> str:
    """Emoji-flagga för språkkod (best-effort). Tom sträng om saknas."""
    if not code:
        return ""
    country = _LANG_TO_COUNTRY.get(code.lower())
    return country_flag_emoji(country) if country else ""


def language_name_sv(code: str | None) -> str:
    """Svenskt språknamn för en ISO 639-1-kod (eller koden själv om okänd)."""
    if not code:
        return ""
    code = code.lower()
    name = _sv_locale().languages.get(code)
    return name or code


def language_display(code: str | None) -> str:
    """Format: '🇸🇪 svenska'. Tom sträng om kod saknas."""
    if not code:
        return ""
    name = language_name_sv(code)
    flag = language_flag_emoji(code)
    return f"{flag} {name}".strip()


@lru_cache(maxsize=1)
def all_languages() -> list[dict]:
    """ISO 639-1-koder med svenska namn + ev. flagga, sorterade på namn."""
    sv = _sv_locale()
    rows = []
    for code, name in sv.languages.items():
        if len(code) != 2 or not code.isalpha():
            continue
        flag = language_flag_emoji(code)
        rows.append(
            {
                "code": code,
                "name": name,
                "label": f"{flag} {name}".strip(),
                "display": f"{flag} {name}".strip(),
            }
        )
    rows.sort(key=lambda r: r["name"])
    return rows
