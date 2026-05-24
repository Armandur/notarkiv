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

Fyra huvudsakliga koncept:

1. **Notpost** (`pieces`) - en katalogpost för ett verk i en specifik
   utgåva/arrangemang
2. **Lagringsplats** (`storage_locations` + `storage_units`) - var noten
   finns, fysiskt eller digitalt
3. **Placering** (`piece_placements`) - många-till-många: en not kan
   finnas på flera platser, en plats innehåller flera noter
4. **Tagg** (`tags` + `piece_tags`) - liturgisk användning, tillfällen,
   fria etiketter

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
    cover_image_path TEXT,                 -- relativ sökväg i IMAGES_PATH
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER REFERENCES users(id)
);

CREATE INDEX idx_pieces_composer ON pieces(composer);
CREATE INDEX idx_pieces_title ON pieces(title);
```

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

### `storage_units` - Förvaringsenhet (nästlad)

```sql
CREATE TABLE storage_units (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES storage_locations(id),
    parent_id INTEGER REFERENCES storage_units(id),
    name TEXT NOT NULL,
    kind TEXT,                             -- hylla, parm, lada, mapp, skap, sharepoint_mapp, teams_kanal, ovrigt
    url TEXT,                              -- valfri, för digitala enheter
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
    composer,
    arranger,
    lyricist,
    notes,
    content='pieces',
    content_rowid='id'
);

-- Triggers för att hålla FTS-indexet i synk
CREATE TRIGGER pieces_ai AFTER INSERT ON pieces BEGIN
    INSERT INTO pieces_fts(rowid, title, original_title, composer, arranger, lyricist, notes)
    VALUES (new.id, new.title, new.original_title, new.composer, new.arranger, new.lyricist, new.notes);
END;
CREATE TRIGGER pieces_ad AFTER DELETE ON pieces BEGIN
    INSERT INTO pieces_fts(pieces_fts, rowid, title, original_title, composer, arranger, lyricist, notes)
    VALUES('delete', old.id, old.title, old.original_title, old.composer, old.arranger, old.lyricist, old.notes);
END;
CREATE TRIGGER pieces_au AFTER UPDATE ON pieces BEGIN
    INSERT INTO pieces_fts(pieces_fts, rowid, title, original_title, composer, arranger, lyricist, notes)
    VALUES('delete', old.id, old.title, old.original_title, old.composer, old.arranger, old.lyricist, old.notes);
    INSERT INTO pieces_fts(rowid, title, original_title, composer, arranger, lyricist, notes)
    VALUES (new.id, new.title, new.original_title, new.composer, new.arranger, new.lyricist, new.notes);
END;
```

Använd `pieces_fts MATCH 'query'` för fritextsökning. Stöder
prefixmatchning med `query*`.

### `scan_sessions` - Spårning av skanningar (valfritt, för debugging)

```sql
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    image_path TEXT NOT NULL,
    ocr_provider TEXT NOT NULL,            -- claude_vision, tesseract, hybrid
    raw_response TEXT,                     -- json från provider
    resulting_piece_id INTEGER REFERENCES pieces(id),  -- null om kasserad
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Användbart för:
- Felsökning ("varför extraherade den fel?")
- Senare träning eller fine-tuning
- Audit ("vem skannade in vilken not när?")

Kan vara tomt initialt - lägg in om/när det behövs.

## Dubblettkoll - logik

Vid en ny skanning, innan post sparas, sök efter potentiella dubbletter:

```sql
SELECT * FROM pieces
WHERE LOWER(title) = LOWER(?)
  AND (composer IS NULL OR LOWER(composer) = LOWER(?))
  AND (arranger IS NULL OR LOWER(arranger) = LOWER(?));
```

Om träff: visa "Liknande post finns redan: [link]. Vill du lägga till
placering där istället för att skapa ny post?"

V2: ersätt med trigram-similaritet eller embeddings för fuzzy match.

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
