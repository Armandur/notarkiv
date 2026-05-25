"""Hjälpare för land: ISO-koder → svenskt namn + emoji-flagga.

Mappning hämtad från Wikipedia/SCB:s landlista. Inte komplett (~70 länder)
men täcker de mest sannolika kompositörsländerna. Komplettera vid behov.
"""

# Svenska namn för vanliga länder. Fall tillbaka till ISO-koden för okända.
COUNTRY_NAMES_SV = {
    "AT": "Österrike",
    "AU": "Australien",
    "BE": "Belgien",
    "BG": "Bulgarien",
    "BR": "Brasilien",
    "CA": "Kanada",
    "CH": "Schweiz",
    "CL": "Chile",
    "CN": "Kina",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CU": "Kuba",
    "CY": "Cypern",
    "CZ": "Tjeckien",
    "DE": "Tyskland",
    "DK": "Danmark",
    "EE": "Estland",
    "EG": "Egypten",
    "ES": "Spanien",
    "FI": "Finland",
    "FR": "Frankrike",
    "GB": "Storbritannien",
    "GR": "Grekland",
    "HU": "Ungern",
    "IE": "Irland",
    "IL": "Israel",
    "IN": "Indien",
    "IR": "Iran",
    "IS": "Island",
    "IT": "Italien",
    "JP": "Japan",
    "KR": "Sydkorea",
    "LT": "Litauen",
    "LU": "Luxemburg",
    "LV": "Lettland",
    "MX": "Mexiko",
    "NL": "Nederländerna",
    "NO": "Norge",
    "NZ": "Nya Zeeland",
    "PE": "Peru",
    "PL": "Polen",
    "PT": "Portugal",
    "RO": "Rumänien",
    "RS": "Serbien",
    "RU": "Ryssland",
    "SE": "Sverige",
    "SK": "Slovakien",
    "TR": "Turkiet",
    "UA": "Ukraina",
    "US": "USA",
    "VA": "Vatikanstaten",
    "ZA": "Sydafrika",
}


def country_flag_emoji(code: str | None) -> str:
    """Konvertera ISO 3166-1 alpha-2 till emoji-flagga via regional indicator symbols.

    Returnerar tom sträng för None eller ogiltig kod.
    """
    if not code or len(code) != 2 or not code.isalpha():
        return ""
    code = code.upper()
    return chr(0x1F1E6 + ord(code[0]) - ord("A")) + chr(
        0x1F1E6 + ord(code[1]) - ord("A")
    )


def country_name_sv(code: str | None) -> str:
    """Returnera svenskt landsnamn eller koden själv om okänt."""
    if not code:
        return ""
    code = code.upper()
    return COUNTRY_NAMES_SV.get(code, code)


def country_display(code: str | None) -> str:
    """Format: '🇩🇪 Tyskland'. Tom sträng om kod saknas."""
    if not code:
        return ""
    flag = country_flag_emoji(code)
    name = country_name_sv(code)
    return f"{flag} {name}".strip()
