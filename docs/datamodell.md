# Datamodell

Datamodellen för Notarkiv, beskriven både som SQLModel-klasser och
underliggande SQLite-schema. Detta är källan till sanning. Vid ändring:
uppdatera detta dokument i samma commit som modelländringen i
`app/models/`.

**Implementation**: SQLModel (Pydantic + SQLAlchemy) i `app/models/`.
Schemat nedan visas som rå SQL för tydlighet, men koden definierar
SQLModel-klasser och låter SQLAlchemy generera SQL.

**Portabilitet**: schemat är designat för att fungera på både SQLite
och PostgreSQL utan ändringar. Se `postgres-migration.md` för listan
av medvetna kompromisser och migrationsstrategi.

## Översikt

Huvudsakliga koncept:

1. **Notpost** (`pieces`) - en katalogpost för ett verk i en specifik
   utgåva/arrangemang
2. **Bilder** (`piece_images`) - en eller flera bilder per not (omslag,
   baksida, försättsblad m.m.)
3. **Person** (`people` + `piece_contributors`) - kompositör, arrangör,
   textförfattare som egna entiteter med relation till piece via roll
4. **Lagringsplats** (`storage_locations` + `storage_units`) - var noten
   finns, fysiskt eller digitalt. Enhetstyper i `unit_kinds`.
5. **Placering** (`piece_placements`) - många-till-många: en not kan
   finnas på flera platser
6. **Tagg** (`tags` + `piece_tags`) - liturgisk användning, tillfällen,
   fria etiketter
7. **Skanning** (`scan_sessions` + `scan_session_images`) - varje
   uppladdning blir en session; OCR + MB-berikning körs som arq-jobb.
   Avvisade scans markeras `discarded` istället för att raderas.
8. **Inventeringstillfälle** (`inventory_sessions`) - grupperar
   skanningar gjorda i samma sammanhang, med planerad plats och logg
9. **Inventeringskontroll** (`inventory_checks`) - en kontroll per
   placering inom en session: ✓ hittad / ⚠ avvikande antal / ✗ saknas
10. **Inställningar** (`app_settings`) - runtime-värden ändringsbara via
    admin-UI (Anthropic-nyckel, Claude-modell, MB User-Agent)

## Tabeller

### `users` - Användare

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT,
    password_hash TEXT NOT NULL,           -- bcrypt
    role TEXT NOT NULL DEFAULT 'reader',   -- reader, editor, admin
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);

CREATE INDEX idx_users_username ON users(username);
```

Roller:
- `reader`: kan söka och se allt
- `editor`: kan dessutom skapa/redigera notposter och placeringar
- `admin`: kan dessutom hantera användare och lagringsplatser

E-post är valfritt (kan användas för lösenordsåterställning senare,
inte för login).

### `pieces` - Notpost

```sql
CREATE TABLE pieces (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    original_title TEXT,                   -- om titeln är översatt
    composer TEXT,
    arranger TEXT,
    lyricist TEXT,
    language TEXT,                         -- ISO 639-1, t.ex. 'sv', 'la', 'en'
    voicing TEXT,                          -- SATB, SAB, SSA, unison, solo, etc.
    accompaniment TEXT,                    -- a_cappella, piano, organ, other
    publisher TEXT,
    edition_number TEXT,                   -- förlagsnummer/edition
    psalm_number INTEGER,                  -- valfritt, om kopplat till psalmboken
    difficulty INTEGER,                    -- 1-5, valfritt
    duration_seconds INTEGER,              -- valfritt
    copyright_status TEXT,                 -- original, licensed_copy, public_domain, unknown
    musicbrainz_work_id TEXT,              -- MBID om matchat
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER REFERENCES users(id)
);

CREATE INDEX idx_pieces_composer ON pieces(composer);
CREATE INDEX idx_pieces_title ON pieces(title);
```

Notpost har inget eget `cover_image_path`-fält - alla bilder finns i
`piece_images` med `sort_order` (lägst = primär).

`contributors_cache` är en denormaliserad sökbar textrad byggd från
`piece_contributors`-länkarna, t.ex. `"Felix Mendelssohn (composer); Hugo Distler (arranger)"`.
Genereras om vid varje spara av piece eller person. FTS5 indexerar den.

### `people` - Personer (kompositörer, arrangörer, textförfattare)

```sql
CREATE TABLE people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,                    -- "Felix Mendelssohn"
    sort_name TEXT NOT NULL,               -- "Mendelssohn, Felix"
    birth_year INTEGER,
    death_year INTEGER,
    biography TEXT,
    wikipedia_url TEXT,
    musicbrainz_artist_id TEXT,            -- MBID från MusicBrainz
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_people_name ON people(name);
CREATE INDEX idx_people_sort_name ON people(sort_name);
CREATE INDEX idx_people_mbid ON people(musicbrainz_artist_id);
```

`sort_name` derivas automatiskt från `name` ("Felix Mendelssohn" ->
"Mendelssohn, Felix") via `services/people.py::derive_sort_name` om
det inte sätts explicit.

### `piece_contributors` - Notpost ↔ Person med roll

```sql
CREATE TABLE piece_contributors (
    id INTEGER PRIMARY KEY,
    piece_id INTEGER NOT NULL REFERENCES pieces(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id),
    role TEXT NOT NULL,                    -- composer, arranger, lyricist, editor, conductor, other
    sort_order INTEGER NOT NULL DEFAULT 0
);
```

Samma person kan vara både kompositör och arrangör av samma piece
(två rader, olika roll). `sort_order` styr ordningen inom samma roll
för "Hugo Distler & John Rutter".

### `piece_images` - Bilder per not

```sql
CREATE TABLE piece_images (
    id INTEGER PRIMARY KEY,
    piece_id INTEGER NOT NULL REFERENCES pieces(id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,              -- relativ sökväg i IMAGES_PATH
    kind TEXT NOT NULL DEFAULT 'cover',    -- cover, back, title_page, inside, other
    label TEXT,                            -- fri text, t.ex. "Försättsblad"
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_piece_images_piece ON piece_images(piece_id);
```

- Den med lägst `sort_order` är primär (visas som thumbnail i listor).
- "Gör primär" flyttar bilden till `sort_order=0` och skiftar övriga uppåt.
- Rotation: PIL `Image.rotate(expand=True)` skriver om filen samt
  regenererar thumbnail. Klientside-rotation görs också i mobil
  quick-scan via HTML5 canvas före upload.

**Designval**:

- `composer`, `arranger`, `lyricist` är separata textfält och *inte*
  egna entiteter med relationer. Motivering: enkelhet i MVP, inga
  many-to-many. Om vi senare vill ha "alla noter av Sven-David
  Sandström" kan vi normalisera då.
- `voicing` som TEXT (inte enum-tabell): variation är stor och nya
  besättningar dyker upp. Använd en konsekvent enum i Python-koden
  istället för i databasen.
- `cover_image_path` lagras som *relativ* sökväg, prependeras med
  `IMAGES_PATH` vid läsning. Gör flytt av lagring enklare.

### `storage_locations` - Lagringsplats (rum/system)

```sql
CREATE TABLE storage_locations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'physical', -- physical, digital
    description TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Exempel:
- `Notarkivet` (physical)
- `Sakristian` (physical)
- `SharePoint` (digital)
- `Teams` (digital)

### `unit_kinds` - Typ av förvaringsenhet

```sql
CREATE TABLE unit_kinds (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Seedas alltid med `hylla`, `pärm`, `låda`, `mapp`. Nya kinds skapas via
autocomplete-UI:t i formuläret för ny enhet (`/storage/unit-kinds/search`
+ `POST /storage/unit-kinds`). Dubletter blockeras (unique constraint +
idempotent POST returnerar befintlig).

### `storage_units` - Förvaringsenhet (nästlad)

```sql
CREATE TABLE storage_units (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES storage_locations(id),
    parent_id INTEGER REFERENCES storage_units(id),
    name TEXT NOT NULL,
    kind_id INTEGER REFERENCES unit_kinds(id),
    sort_order INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,   -- soft delete
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_storage_units_location ON storage_units(location_id);
CREATE INDEX idx_storage_units_parent ON storage_units(parent_id);
```

**Nästling**: `parent_id` är null för enheter direkt under en location,
annars peka på en annan storage_unit i samma location. Inga cykler
(applikationen ansvarar för att inte skapa dem).

**Sökväg**: beräknas vid behov med rekursiv CTE eller i Python.
Cacha *inte* i kolumn - blir ur synk.

Exempel:
```
Notarkivet (location)
├── Hylla A (unit, parent=null)
│   ├── Pärm A1 (unit, parent=Hylla A)
│   └── Pärm A2 (unit, parent=Hylla A)
└── Skåp 2 (unit, parent=null)
    └── Låda 5 (unit, parent=Skåp 2)

SharePoint (location, kind=digital)
└── Körbiblioteket > Advent (unit, parent=null, url=https://...)
```

**Soft delete via `archived`**: om någon raderar en enhet med
placeringar ska det inte gå tyst. Antingen blockera (validering)
eller markera arkiverad och dölj från default-vyer.

### `piece_placements` - Placering av not

```sql
CREATE TABLE piece_placements (
    id INTEGER PRIMARY KEY,
    piece_id INTEGER NOT NULL REFERENCES pieces(id) ON DELETE CASCADE,
    storage_unit_id INTEGER NOT NULL REFERENCES storage_units(id),
    copies INTEGER,                        -- null för digital, antal för fysisk
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (piece_id, storage_unit_id)
);

CREATE INDEX idx_placements_piece ON piece_placements(piece_id);
CREATE INDEX idx_placements_unit ON piece_placements(storage_unit_id);
```

`copies` är nullable: ingen menings för digitala placeringar.
Applikationen säkerställer att det är null för digitala units och
en positiv heltal för fysiska.

`UNIQUE (piece_id, storage_unit_id)` förhindrar dubbla rader för
samma not på samma plats. Ändra `copies` istället för att lägga till
en till rad.

### `tags` - Taggar

```sql
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,                    -- liturgical, occasion, free
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE piece_tags (
    piece_id INTEGER NOT NULL REFERENCES pieces(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (piece_id, tag_id)
);

CREATE INDEX idx_piece_tags_tag ON piece_tags(tag_id);
```

`kind` skiljer på tre typer:
- `liturgical`: kyrkoåret - advent, jul, fasta, påsk, pingst, trinitatistid, allmän
- `occasion`: tillfällen - begravning, bröllop, dop, konfirmation, allmän_gudstjanst
- `free`: fria taggar användare hittar på - barnkör, luciatåg, julbordskonsert

Seedas vid första startup med en standarduppsättning för `liturgical`
och `occasion`. `free` är tomt initialt.

### `pieces_fts` - Fulltextsökning

```sql
CREATE VIRTUAL TABLE pieces_fts USING fts5(
    title,
    original_title,
    contributors_cache,
    notes,
    content='pieces',
    content_rowid='id'
);

-- Triggers för att hålla FTS-indexet i synk
CREATE TRIGGER pieces_ai AFTER INSERT ON pieces BEGIN
    INSERT INTO pieces_fts(rowid, title, original_title, contributors_cache, notes)
    VALUES (new.id, new.title, new.original_title, new.contributors_cache, new.notes);
END;
-- (motsvarande pieces_ad och pieces_au; se app/db.py för faktiska definitioner)
```

Använd `pieces_fts MATCH 'query'` för fritextsökning. Stöder
prefixmatchning med `query*`.

### `scan_sessions` - Skanningar

```sql
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    image_path TEXT NOT NULL,              -- primärbild för OCR
    ocr_provider TEXT NOT NULL,            -- claude_vision, tesseract, hybrid
    status TEXT NOT NULL DEFAULT 'pending', -- pending, extracting, enriching, done, failed
    raw_response TEXT,                     -- JSON från OCR-providern
    musicbrainz_suggestion TEXT,           -- JSON-array av förslag
    error_message TEXT,                    -- om status=failed
    pre_placement_unit_id INTEGER REFERENCES storage_units(id),
    pre_placement_copies INTEGER,
    inventory_session_id INTEGER REFERENCES inventory_sessions(id),
    resulting_piece_id INTEGER REFERENCES pieces(id),  -- null om ej granskad/sparad
    discarded INTEGER NOT NULL DEFAULT 0,  -- soft-delete, döljs från kön
    discarded_at TIMESTAMP,
    discard_reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
```

`resulting_piece_id IS NULL AND status='done' AND NOT discarded`
definierar granskningskön. Avvisade skanningar visas via en toggle
i UI:t och kan återställas.

### `scan_session_images` - Extra bilder under skanning

```sql
CREATE TABLE scan_session_images (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_scan_session_images_session ON scan_session_images(scan_session_id);
```

Vid quick-scan kan användaren skanna fram, bak, försättsblad osv i en
sittning. Första bilden går till `scan_sessions.image_path` (OCR-mål),
resten lagras här. När piece sparas via granskningsformuläret blir alla
till `PieceImage` med bevarad ordning.

### `inventory_sessions` - Inventeringstillfälle

```sql
CREATE TABLE inventory_sessions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    planned_location_id INTEGER REFERENCES storage_locations(id),
    planned_unit_id INTEGER REFERENCES storage_units(id),
    log TEXT,                              -- append-only fritext med tidsstämplar
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    started_by INTEGER REFERENCES users(id)
);
```

En aktiv session i taget globalt - vid `POST /inventory` med befintlig
aktiv: sätt `ended_at` på den, skapa ny. Skanningar gjorda när en
session är aktiv får automatiskt `inventory_session_id`.

### `inventory_checks` - Per-placeringskontroll

```sql
CREATE TABLE inventory_checks (
    id INTEGER PRIMARY KEY,
    inventory_session_id INTEGER NOT NULL REFERENCES inventory_sessions(id) ON DELETE CASCADE,
    placement_id INTEGER NOT NULL REFERENCES piece_placements(id),
    status TEXT NOT NULL DEFAULT 'not_checked',  -- found, partial, missing, extra
    actual_copies INTEGER,
    notes TEXT,
    checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checked_by INTEGER REFERENCES users(id)
);

CREATE INDEX idx_inventory_checks_session ON inventory_checks(inventory_session_id);
```

Inga unique-constraints - om en placering checkas om läggs en ny rad
till. Senaste rad per `(session, placement)` är gällande status.
Senare kan vi exportera "vad var saknat senast"-rapporter genom att
joina med den senaste raden per placering.

### `app_settings` - Runtime-inställningar

```sql
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by INTEGER REFERENCES users(id)
);
```

Nycklar som används idag: `anthropic_api_key`, `claude_model`,
`musicbrainz_user_agent`, `ocr_provider`. Värden i klartext - skydda
SQLite-filen på OS-nivå. `app/services/app_settings.py` läser via DB med
env-värden som fallback.

## Dubblettkoll - logik

Implementerat i `app/services/duplicates.py::find_duplicates`.

- Laddar alla pieces i minnet (trivialt för 200-1000 noter)
- rapidfuzz.ratio på `title` (viktning 65%)
- rapidfuzz.partial_ratio på `contributors_cache` mot ev. kompositör-input (35%)
- +30 score om `edition_number` matchar exakt (samma utgåva = nästan
  alltid dublett)
- Filtrerar score >= 60, returnerar top 3

Vid review_form-rendering anropas denna med extraherade värden från
OCR. Förslag visas som gul varningsbanner med "Lägg till placering"-
knapp som expandererar inline-form. POST `/scan/{id}/add-placement/{piece_id}`
kopplar skanningen till befintlig piece.

Skala över 10k noter: byt till Postgres `pg_trgm` GIN-index.

## Migrationsstrategi

**Före prod (nuvarande läge): nuke + seed.** Vid schemaändringar
raderar vi databasen och kör om seed-skriptet. Inga ALTER-guards
används. Se `seed-data.md` för seed-strukturen och CLI-kommandona.

```bash
python -m app.cli db reset --seed
```

Modellen utvecklas snabbt under utveckling - vi sparar mycket tid på
att inte hantera migrationer för tillfällig data.

**Efter prod**: `init_db()` blir idempotent med ALTER-guards för
framtida kolumner:

```python
def init_db(engine):
    SQLModel.metadata.create_all(engine)  # skapar saknade tabeller
    _apply_column_guards(engine)           # ALTER för nya kolumner
    _ensure_fts_objects(engine)            # FTS5-tabell + triggers (SQLite only)
    _seed_default_tags_if_missing(engine)  # idempotent
```

Tröskeln för bytet: när användaren registrerar skarp data eller en
backup tas första gången. Se `seed-data.md` för fullständig checklista.

När projektet är moget nog att flytta till Postgres kan vi byta till
Alembic - men inte tidigare. Se `postgres-migration.md`.

## Indexering

Skapa index för:
- Fält som ofta filtreras på (`composer`, `voicing`, `language`)
- Foreign keys (`location_id`, `parent_id`, `piece_id`)
- Sökning sker via FTS5 - inget eget index på `title` behövs förutom
  som komplement för exakta lookups

Lägg inte till index speculativt. Om en query blir långsam vid 1000+
rader: mät, sedan indexera.
