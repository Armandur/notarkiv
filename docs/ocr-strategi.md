# OCR/Vision-strategi

Hur vi extraherar strukturerad metadata från ett notomslag. Vi stödjer
flera providers via ett gemensamt interface och låter användaren välja
per skanning eller via env-default.

## Körning som bakgrundsjobb

OCR-extraktion tar typiskt 2-15 sekunder. Det är för långt för en
synkron HTTP-request, särskilt på mobil. Skanning körs därför som ett
arq-jobb:

1. Användaren laddar upp bild via `POST /scan/upload`
2. Bilden sparas, ett `scan_session`-id skapas
3. Ett arq-jobb `extract_metadata_job(scan_session_id)` queuas
4. Endpointen returnerar omedelbart med en granskningsside-URL som
   pollas via HTMX (`hx-get` med `hx-trigger="every 1s"`)
5. När jobbet är klart visas resultatet och eventuell MusicBrainz-träff
   som förslag

Detta gör batch-läge i v2 trivialt (kö flera jobb, visa progress) och
gör UI:t responsivt även när Anthropic API är trögt.

## Mål

Givet en bild av ett notomslag, returnera ifyllda fält för:

- Titel (och eventuell originaltitel)
- Kompositör
- Arrangör (om olika från kompositör)
- Textförfattare (om relevant)
- Besättning (SATB, SAB, etc.)
- Ackompanjemang (a cappella, piano, orgel)
- Förlag
- Förlagsnummer/edition
- Språk på texten

Inget av detta är obligatoriskt - vi accepterar partiella resultat.
Människan kompletterar.

## Interface

```python
# app/services/ocr/base.py
from typing import Protocol
from pydantic import BaseModel

class ExtractedMetadata(BaseModel):
    title: str | None = None
    original_title: str | None = None
    composer: str | None = None
    arranger: str | None = None
    lyricist: str | None = None
    voicing: str | None = None
    accompaniment: str | None = None
    publisher: str | None = None
    edition_number: str | None = None
    language: str | None = None

    raw_text: str | None = None        # användbart för Tesseract-debug
    confidence: float | None = None    # 0-1, om provider rapporterar
    provider: str
    cost_estimate_sek: float | None = None  # ungefär, för UI-visning

class OCRProvider(Protocol):
    name: str
    async def extract(self, image_bytes: bytes) -> ExtractedMetadata: ...
```

## Providers

### `claude_vision` (default)

Modell: `claude-haiku-4-5` (snabb, billig, ändå utmärkt på structured
extraction).

Flöde:
1. Resize bild till max 1568px på längsta sidan (Anthropic
   rekommendation, sparar tokens)
2. Skicka som `base64`-kodad image-content till Messages API
3. Använd `tool_use` med ett "extract_score_metadata"-tool för att
   tvinga strukturerad output i samma schema som `ExtractedMetadata`
4. Parsa svaret, mappa till `ExtractedMetadata`

Prompt-skiss:
```
Du är expert på att extrahera metadata från klassiska och moderna
notomslag. Användaren visar en bild av en framsida på en notpost.

Extrahera följande fält så noggrant du kan. Lämna fält null om du
inte är säker - gissa inte. Tänk på att:
- Titlar är ibland flerspråkiga (originaltitel + översättning)
- "Av X" eller "Music by X" anger kompositör
- "Arr. X" eller "Arranged by X" anger arrangör
- Besättning står ofta som "SATB", "for mixed choir", "SSA", etc.
- Förlagsnummer är oftast i hörnet, t.ex. "GH-1234" eller "BA 5678"

Notomslaget är på: [bild bifogad]

Returnera via tool_use: extract_score_metadata.
```

Tool definition:
```json
{
  "name": "extract_score_metadata",
  "input_schema": {
    "type": "object",
    "properties": {
      "title": {"type": "string"},
      "original_title": {"type": "string"},
      "composer": {"type": "string"},
      "arranger": {"type": "string"},
      "lyricist": {"type": "string"},
      "voicing": {"type": "string"},
      "accompaniment": {"type": "string", "enum": ["a_cappella", "piano", "organ", "other"]},
      "publisher": {"type": "string"},
      "edition_number": {"type": "string"},
      "language": {"type": "string"}
    }
  }
}
```

Caching: använd Anthropics prompt-cache för system-prompten (den är
samma för varje skanning) - sparar tokens vid många snabba skanningar
i följd. Bilden kan inte cachas (varierar).

### `tesseract` (fallback)

Använder pytesseract med språkpacken `swe+eng+lat`.

Flöde:
1. Preprocessering: gråskala, mild kontraststräckning, ev. deskew
2. `pytesseract.image_to_string(img, lang="swe+eng+lat")`
3. Heuristisk parsning av råtext:
   - Första 1-3 raderna brukar vara titel
   - Leta efter mönster som "Music by", "Arr.", "Text:", "SATB", etc.
   - Förlagsnummer hittas via regex (`[A-Z]{1,4}[\s-]?\d+`)
4. Returnera `ExtractedMetadata` med `raw_text` ifyllt så användaren
   kan se hela texten i granskningsformuläret

Kvaliteten på heuristisk parsning är begränsad. Tesseract-läget är
främst:
- Offline/intern-only skanning där inget får skickas externt
- Råtext-dump till människan, som fyller i fält manuellt
- Backup om Anthropic API är nere

### `hybrid` (Tesseract + Claude för strukturering)

Bilden går aldrig till Anthropic - bara texten.

Flöde:
1. `tesseract` extraherar råtext
2. Skicka råtexten + samma extract-tool till `claude-haiku-4-5` (utan
   vision, bara text)
3. Returnera strukturerad output

Detta är billigare än ren vision (text-tokens kostar ~10x mindre än
bild-tokens). Lägger till komplexitet utan att vara *nödvändigt* för
projektets skala (1000 noter à ren vision är fortfarande under 20 SEK).
Implementera för fullständighet, men default-rekommendation är ren
`claude_vision`.

Kvalitet vs ren `claude_vision`:
- På modernt tryck: nästan likvärdigt
- På stiliserade titlar, handskrift, dekorerade omslag: betydligt sämre
  (eftersom Tesseract redan gjort fel)

## Val mellan providers

### Default

Sätts via env: `OCR_PROVIDER=claude_vision` (default i `.env.example`).

### Per skanning i UI

I granskningsformuläret en "Skanna om med..."-dropdown med tre val.
Användbart för att jämföra resultat på samma bild när extraktion blivit
fel.

### När fallback aktiveras automatiskt

Inte i MVP. Om vi senare vill ha automatisk fallback:
- Om `claude_vision` returnerar fel (timeout, rate limit, 5xx) - prova
  `tesseract` automatiskt
- Logga händelsen så vi förstår frekvensen

## Bildhantering

### Före OCR

- EXIF-rotation tillämpas korrekt (många mobilbilder är liggande i
  data men porträtt i metadata)
- Resize till max 1568px för Claude Vision (Anthropic rekommendation)
- För Tesseract: gråskala + mild kontraststräckning hjälper
- Inget OCR på originalet - alltid på en preprocessad kopia

### Vad sparas

- Originalbild i `data/images/covers/<uuid>.jpg`
- Thumbnail (300px) i `data/images/thumbnails/<uuid>.jpg`
- Båda sökvägarna är *relativa* mot `IMAGES_PATH` i databasen

### Inget hyperdetalj

Vi sparar inga skannade sidor av noten utöver omslaget. Om någon
laddar upp en hel PDF i framtiden (V3) är det en separat funktion.

## Kostnad

Uppdaterad uppskattning per skanning vid `claude-haiku-4-5`:

| Resurs              | Tokens (typ) | Kostnad SEK (typ) |
|---------------------|--------------|-------------------|
| System prompt       | ~300         | <0.001 (cached)   |
| Bild (1568px)       | ~1000        | ~0.01             |
| Output (tool_use)   | ~150         | ~0.002            |
| Totalt per skan     | -            | ~0.012            |

För hela katalogen (200-1000 noter): 2-12 SEK. Försumbart.

Tesseract: 0 SEK externt, men CPU-tid. För hybrid kommer ungefär en
tiondel av cost för claude_vision.

## Testning

- Unit-tester för:
  - `_parse_tesseract_output` - heuristisk parsning av kända textstycken
  - Schema-validering av Claude-tool-output
  - Bildpreprocessering (rotation, resize)
- Integration-tester:
  - Mocka Anthropic API-klient, returnera fixerad tool-response
  - Verifiera att `ExtractedMetadata` blir korrekt
- Manuell test:
  - Samling av 20 representativa notomslag i `tests/fixtures/`
  - Skripta en jämförelse: kör alla tre providers, dumpa resultat
  - Använd för regressionstest vid promptändring

## Framtida idéer (ej i MVP)

- **Konfidensbaserad granskning**: om Claude returnerar låg confidence
  på vissa fält, markera dem röda i granskningsformuläret
- **Embeddings för dubblettkoll**: embed `title + composer` med
  Voyage- eller OpenAI-embeddings, jämför mot befintliga poster med
  cosine similarity. SQLite + `sqlite-vss` för vektor-sökning.
- **Återanvänd manuella rättelser**: om användaren ofta rättar "Sandström"
  till "Sven-David Sandström", lär systemet det. Antingen via en
  alias-tabell eller via examples i prompten.
- **MusicBrainz/IMSLP-berikning**: efter extraktion, lookup mot externa
  kataloger för att fylla i kanonisk metadata.
