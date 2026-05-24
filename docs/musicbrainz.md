# MusicBrainz-integration

Vi använder MusicBrainz för att berika OCR-extraherade metadata med
kanoniska namn och relationer. Triggas automatiskt efter OCR-extraktion
i skanningsflödet och visas som förslag i granskningsformuläret.

## Varför MusicBrainz

- Gratis, ingen autentisering, ingen avgift
- Stor täckning för klassisk och kyrklig musik
- Strukturerade relationer: verk -> kompositör, arrangör, opusnummer
- Identifierar samma verk i olika utgåvor (samma "work MBID" oavsett
  vilken förlagsutgåva)
- Kanonisk stavning av kompositörnamn ("Sven-David Sandström" istället
  för "S.D. Sandström" eller "S-D Sandstrom")

Begränsningar att vara realistisk om:
- Lokala/svenska arrangemang av äldre verk finns ofta inte
- Modern komposition från små förlag täcks dåligt
- Folkmusik och visor varierar mycket i täckning
- Vi får träffar oftast på verk, inte specifik utgåva

## Användning i flödet

```
[Skanna omslag] -> [OCR-job] -> [MusicBrainz-job] -> [Granskningsformulär]
                       |              |                      |
                       v              v                      v
                  ExtractedMetadata  Förslag             Användaren ser:
                  (rå från OCR)      (kanoniska)         - OCR-värden ifyllda
                                                         - "Möjlig matchning"-banner
                                                           med "Använd"-knapp
```

MusicBrainz-jobbet körs *efter* OCR-jobbet, kedjat via arq. Användaren
ser OCR-resultaten direkt och MB-förslagen dyker upp via HTMX-poll när
det jobbet också är klart.

## API

MusicBrainz har ett REST-API på `https://musicbrainz.org/ws/2/`.

Endpoints vi använder:

- `GET /work` - sök verk efter namn + kompositör
- `GET /work/{mbid}?inc=artist-rels` - hämta detaljer för ett verk

Sökexempel:
```
GET /work?query=work:"Verleih uns Frieden" AND artist:Mendelssohn&fmt=json
```

Output: lista med matchande verk, varje med MBID, titel, kompositör,
genre, ev. opusnummer.

## Klient

`app/services/musicbrainz.py` implementerar en tunn klient:

```python
class MusicBrainzClient:
    BASE_URL = "https://musicbrainz.org/ws/2"

    def __init__(self, user_agent: str, rate_limit_delay: float = 1.0):
        self.user_agent = user_agent
        self.rate_limit_delay = rate_limit_delay
        self._last_request_at: float = 0
        self._cache = LRUCache(maxsize=1000)

    async def search_work(self, title: str, composer: str | None) -> list[Work]:
        ...

    async def get_work(self, mbid: str) -> Work | None:
        ...
```

### User-Agent

MusicBrainz kräver en identifierande User-Agent-sträng. Sätts via env:

```
MUSICBRAINZ_USER_AGENT=notarkiv/0.1 (din@email.tld)
```

Anrop utan giltig User-Agent blockeras.

### Rate limiting

MusicBrainz tillåter 1 request/sekund som riktlinje. Klienten håller
ett internt tidsstämpel och väntar vid behov. Detta är OK för vårt
användarmönster (en lookup per skanning, sällan parallellt).

### Cachning

Lokalt cache (LRU, 1000 entries) på `(title, composer)`-nyckeln för
sökningar och på `mbid` för detaljhämtningar. Förhindrar duplicerade
anrop när användaren skannar samma verk flera gånger (olika utgåvor)
eller om en bild laddas om.

Cachen är process-lokal (lever i app-processen). För persistent cache
över omstarter: lägg till `musicbrainz_cache`-tabell i SQLite med
TTL-kolumn. Inte i MVP.

## Matchning och förslag

Givet OCR-extraherade `title` och `composer`:

1. Anropa `search_work(title, composer)` med fullständig matchning
2. Om ingen träff, prova `search_work(title, None)` för fuzzy
3. För varje träff (max 3 visas), beräkna en *konfidensscore*:
   - Exakt match på titel: +50
   - Fuzzy match (>80% likhet) på titel: +30
   - Exakt match på kompositör: +30
   - Fuzzy match på kompositör: +15
   - Verk har opusnummer som finns i OCR-text: +10
4. Sortera, visa top 3 med score >= 50

Förslag visas i granskningsformuläret som:

```
[Möjlig matchning från MusicBrainz]
Verleih uns Frieden gnädiglich, Op. 64
av Mendelssohn, Felix
[Använd dessa värden] [Hoppa över]
```

"Använd" fyller i `composer`, `original_title`, ev. korrigerar
`title`-stavning, och sparar MB-länken i `notes` eller en separat
kolumn.

## Datalagring

Två val:

1. **Bara använd MB-data till att fylla i fält** - ingen referens sparas
2. **Spara MBID på notposten** - för senare uppslag och uppdateringar

Vi börjar med (1) i MVP. Lägg till `pieces.musicbrainz_work_id` om/när
vi vill stödja "uppdatera från MusicBrainz" eller bygga en
sammanhängande katalog.

## När MusicBrainz inte hjälper

Stora delar av kyrkliga arrangemang kommer inte att finnas. Det är OK.
Användaren ser bara "Inga MusicBrainz-förslag" och fyller i manuellt
som vanligt.

Logga MB-träffsfrekvens via loguru så vi över tid kan se hur
användbart det är. Om <10% av skanningar får träff är det inte värt
underhållet.

## Testning

- Mock-svar från MB-API:t i `tests/fixtures/musicbrainz/`
- Verifiera rate-limit-uppförande
- Verifiera scoring och sortering av förslag
- Manuell test mot riktiga API:t för en handfull representativa verk
  (kör med pytest-mark `@pytest.mark.integration`, default-skip)

## Framtida idéer

- **Cover Art Archive**-länkar: MusicBrainz har ett systerprojekt med
  omslagsbilder. Kan visas som referens i granskningsformuläret.
- **IMSLP-länkning**: MusicBrainz har länkar till IMSLP där noterna
  finns fritt tillgängliga (för verk i public domain). Bra för att
  hitta digital version av samma verk.
- **Batch-berikning**: kör MusicBrainz-jobb för alla befintliga poster
  som saknar MBID, t.ex. som ett admin-verktyg.
