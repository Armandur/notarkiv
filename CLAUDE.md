# CLAUDE.md - Notarkiv

Detta dokument beskriver projektets stack, struktur och designbeslut för
framtida agenter (och människor). Läs detta först, sedan `docs/` för
detaljer.

## Vad projektet är

En intern webbapp för att katalogisera ett fysiskt notförråd. Användaren
skannar omslag med telefonen, en vision-modell extraherar metadata,
människan granskar, posten sparas. Sedan kan körledare söka och filtrera
för att hitta lämpliga noter.

Volym: 200-1000 noter, några betrodda redigerare, hela arbetslaget som
läsare. Drift på en Unraid-server hemma hos användaren med offsite-backup.

## Stack

Avviker från användarens globala defaulter på några punkter - se
"Designbeslut" nedan för motivering.

- **Backend**: Python 3.12 + FastAPI (uvicorn)
- **Databas**: SQLite via **SQLModel** (Pydantic + SQLAlchemy). Vi kör
  SQLite för MVP men modellkoden ska vara portabel till PostgreSQL.
  Migrationer via `init_db()` med `SQLModel.metadata.create_all()` +
  ALTER-guards där nya kolumner tillkommer. Ingen Alembic.
- **Sökning**: SQLite FTS5 i MVP, abstraherad bakom `services/search.py`
  så den kan bytas ut mot Postgres `pg_trgm`/`tsvector` senare.
- **Templates**: Jinja2
- **Frontend**: **HTMX** + Bootstrap 5 + minimal vanilla JS för
  kamerahantering. Inga npm-paket, ingen bundler. HTMX laddas från
  statiska filer (`/static/js/htmx.min.js`), Bootstrap från CDN eller
  statiska filer.
- **Auth**: Egen användarhantering med användarnamn + lösenord
  (bcrypt-hashning), sessionscookies via Starlette `SessionMiddleware`.
  Användarna skapas av admin och får ett initialt lösenord som de byter
  vid första login.
- **OCR/vision**: Anthropic API (claude-haiku-4-5) som default,
  Tesseract som fallback, hybrid som tredje val. Se
  `docs/ocr-strategi.md`.
- **Berikning**: MusicBrainz som extern källa för kanoniska metadata,
  körs efter OCR-extraktion. Se `docs/musicbrainz.md`.
- **Task queue**: **arq** (async-native, Redis-backed). OCR-jobb och
  MusicBrainz-lookups körs som bakgrundsjobb så att skanning kan
  returnera direkt och UI:t pollar resultat via HTMX.
- **Loggning**: **loguru** för strukturerad loggning. Sentry valfritt
  via env-variabel (`SENTRY_DSN`).
- **Backup**: `scripts/backup.sh` tar `sqlite3 .backup`-snapshot,
  komprimerar och laddar upp till Google Drive via rclone. Bilder
  synkas inkrementellt. Schemaläggs via cron på värden.
  Se `docs/backup.md`.
- **Runtime-inställningar**: `AppSetting`-tabell tillåter ändring av
  API-nyckel, Claude-modell, OCR-default, MB User-Agent via
  `/admin/settings` utan omstart. Env-värden används som fallback.
- **Deployment**: Docker + docker-compose med tre tjänster: `app`,
  `worker` (arq), `redis`. Caddy som reverse proxy om HTTPS behövs,
  annars rakt över Tailscale-IP. Enda image:n används för både web och
  worker - skiljs åt via CMD.
- **Tester**: pytest + httpx async client + factory_boy för
  testfixtures.

## Filstruktur (faktisk)

```
notarkiv/
  app/
    main.py                 # FastAPI-app, lifespan, middleware, routers
    config.py               # Settings (pydantic-settings), env-läsning
    db.py                   # SQLModel engine, FTS5-setup, init_db, reset_db
    auth.py                 # bcrypt-hashning, hjälpfunktioner
    deps.py                 # session, current_user, require_*, CSRF
    middleware.py           # EnsureCSRFTokenMiddleware
    logging_setup.py        # loguru + ev. Sentry-init
    templates_setup.py      # Jinja2-render-hjälpare, flash(), global ctx
    cli.py                  # typer-CLI: db init/reset/seed, users create-*
    seed.py                 # Läser seed_data/*.yaml och fyller DB
    models/
      user.py               # User, Role
      piece.py              # Piece + Voicing/Accompaniment/CopyrightStatus
      piece_image.py        # PieceImage (flera bilder per not)
      person.py             # Person + PieceContributor + ContributorRole
      storage.py            # StorageLocation, StorageUnit, PiecePlacement, UnitKind
      tag.py                # Tag, PieceTag
      scan_session.py       # ScanSession + ScanStatus
      scan_session_image.py # Extra bilder per skanning
      inventory.py          # InventorySession
      inventory_check.py    # InventoryCheck (en check per placering inom session)
      app_setting.py        # Key-value-runtime-inställningar
    routes/
      pages.py              # GET / med översikt
      auth.py               # login/logout/change-password
      pieces.py             # CRUD, multi-bildhantering, MB-omsökning, taggar, placeringar (CRUD)
      people.py             # Lista, detalj med "som composer/arranger" + redigera/radera
      scan.py               # /scan (vanlig), /scan/quick (mobil), /scan/queue, /scan/{id}/*
                            # /scan/{id}/discard|restore för avvisning, manuell MB-sökning
      storage.py            # CRUD för locations, units, unit-kinds (autocomplete)
      inventory.py          # CRUD för inventeringstillfällen + logg + per-enhet check-läge
      admin/
        users.py            # Listning, roller, lösenord, radering
        settings.py         # API-nyckel, Claude-modell, OCR-default, MB User-Agent
    services/
      ocr/
        base.py             # OCRProvider-protokoll, ExtractedMetadata, get_provider
        tesseract.py        # Lokal OCR med swe/eng/lat
        claude_vision.py    # Anthropic Vision via tool_use
      musicbrainz.py        # Rate-limited klient + LRU-cache + scoring (rapidfuzz)
      app_settings.py       # Hämta runtime-värden (DB med env-fallback)
      inventory.py          # get_active_session, append_log
      people.py             # find_or_create_person, replace_contributors, parse_names_field
      duplicates.py         # find_duplicates: fuzzy-matchning vid skanning
    tasks/
      __init__.py           # get_pool/close_pool för web-processen
      worker.py             # arq WorkerSettings
      ocr_jobs.py           # extract_metadata_job (OCR + MB-berikning)
    utils/
      images.py             # EXIF, JPEG, resize, thumbnail, rotate_saved_image, delete_saved_image
    templates/
      base.html             # Navbar med badges, lightbox (med grupp-nav), confirm-modal
      auth/                 # login.html, change_password.html
      pages/                # index.html
      pieces/
        list.html           # Kort- och list-vy via ?view=
        detail.html         # Read-orienterad: metadata, galleri, placeringar, taggar
        edit.html           # Metadata + bilder + MB-sökning (allt redigerbart)
        new.html            # Manuell skapelse med valfri placering
        _musicbrainz_modal.html  # HTMX-modal med söksformulär + förslag
        _tag_modal.html, _tag_pill.html  # Tagghantering
      scan/
        capture.html        # Vanlig (dator) skanningssida
        quick.html          # Mobil snabbskanning, multi-bild + rotation
        processing.html     # HTMX-pollad statussida efter upload
        review.html         # Granskningsformulär (OCR + MB + dubbletter + "spara och nästa")
        queue.html          # Granskningskö (kort/list-toggle, avvisa/återställ)
        _status.html        # HTMX-fragment
        _musicbrainz_modal.html  # Manuell MB-sökning från granskning
      people/               # list.html, detail.html
      storage/
        manage.html, _tree.html, _unit_form.html, _kind_results.html
      inventory/
        list.html, detail.html
        check_pick.html, check.html, _check_row.html  # Per-enhet inventeringsläge
      admin/
        users.html, settings.html
    static/
      css/custom.css
      js/htmx.min.js
  data/                     # Volym-mappad
    notarkiv.db
    images/{covers,thumbnails}/
  seed_data/                # YAML för db seed
    tags.yaml, unit_kinds.yaml
    (users.yaml, storage_locations.yaml - läggs till av användaren)
  docs/
    datamodell.md, ocr-strategi.md, musicbrainz.md
    postgres-migration.md, seed-data.md
  pyproject.toml, uv.lock
  Dockerfile, docker-compose.yml
  .env.example, .gitignore
```

Förbered för uppdelning så fort en fil närmar sig 400-500 rader. Se
användarens globala regler i `~/.claude/CLAUDE.md`.

## Centrala designbeslut

### 1. Skannings-flödet är trestegs och asynkront

1. Användaren tar bild i webbapp via `<input capture="environment">`
2. Bild laddas upp, ett OCR-jobb startas via arq och returnerar
   omedelbart med ett job-ID. UI:t pollar jobbstatus via HTMX
   (`hx-get="/scan/jobs/{id}" hx-trigger="every 1s"`)
3. När jobbet är klart hämtas resultat och visas i granskningsformulär
4. Användaren granskar/kompletterar och sparar

**Steg 3 är icke-förhandlingsbart.** Auto-extraktion blir aldrig 100%
korrekt. Människan i loopen är det som gör databasen användbar.
Designa flödet så att granskningsformuläret är så snabbt som möjligt
att gå igenom.

**Asynkronisering via arq** gör batch-läge (v2) trivialt - kö flera
jobb, visa progress på en sida, granska en i taget när de blir klara.

**MusicBrainz-berikning** triggas också via en arq-task efter
OCR-extraktion. Resultatet visas som förslag i granskningsformuläret
("Möjlig matchning: *Mendelssohn, Felix - Verleih uns Frieden*. Använd
denna metadata?").

### 2. Lagringsplatser: en modell för fysisk OCH digital

Tidigare iteration hade separata tabeller för fysiska placeringar och
digitala filer. Vi gick istället över till en enhetlig modell:

- `storage_locations` har ett `kind`-fält: `physical` eller `digital`
- `storage_units` är nästlade godtyckligt djupt via `parent_id`
- Digitala enheter får en valfri `url` för direktlänk till mappen
- `piece_placements` har `copies` som är null för digitala

Detta gör att SharePoint-mappar, Teams-kanaler, fysiska pärmar och lådor
representeras likvärdigt. Användaren får ett enhetligt UI för "var finns
denna not" oavsett om svaret är fysiskt eller digitalt.

**Vad vi medvetet inte gör**: ingen hård integration mot SharePoint/Graph
API. Bara en notering om att noten finns där, med en URL som ger
användaren ett klick dit. Beslut: bekräftat av användaren 2026-05-24.

Se `docs/datamodell.md` för fullständigt schema.

### 3. OCR/vision via strategy pattern

`OCRProvider`-interface med tre implementationer: `tesseract`,
`claude_vision`, `hybrid`. Val sker via `OCR_PROVIDER` env-variabel,
men användaren kan välja per-skanning i UI:t (radioknappar) för att
jämföra resultat.

**Default = claude_vision.** Motivering:
- Stilren extraktion direkt till strukturerad JSON
- Notomslag har ofta stiliserad typografi som Tesseract hanterar dåligt
- Totalkostnad för 1000 noter är 2-20 SEK, försumbart
- Användaren har internet vid skanning (Unraid hemma)

Tesseract finns för:
- Fallback om internet är nere
- Känsliga/privata noter som inte ska skickas externt
- Jämförelse vid utveckling

Hybrid (Tesseract OCR + Claude för strukturering av textsträng) är
implementerat men inte default - läggs in för helhetens skull och kan
bli intressant om kostnad någonsin blir ett problem.

Se `docs/ocr-strategi.md` för detaljer.

### 4. Inga PDF/MP3-uppladdningar - bara bilder

Vi sparar bilder (omslag, baksida, försättsblad osv) per not via
PieceImage, men användare laddar inte upp PDF-partitur, MusicXML eller
övningsspår till systemet. Digital tillgång till själva noten anges via
en placering i en digital storage_unit (SharePoint-länk osv).

Motivering:
- Användarens önskan: bara *notering* om digital placering, inte hantering
- Undviker upphovsrättsfrågor (Bonus Copyright täcker kopiering, inte
  nödvändigtvis distribution via tredjepartstjänst)
- Förenklar säkerhet, backup, lagring

Om behovet växer kan vi senare lägga till en `piece_files`-tabell
separat. Strukturen för "var finns digitalt" via storage_units räcker
för nu.

### 7. Två-personers skanningsflöde

Person 1 skannar via mobil (`/scan/quick`) - tar bilder, kan rotera och
lägga till fler bilder (fram/bak/försättsblad) före upload, väljer ev.
placering. Person 2 sitter vid dator och plockar från `/scan/queue` -
listan över skanningar utan tillhörande Piece. Vid spara skapas alla
bilder som PieceImage.

Modellstöd: `ScanSession.pre_placement_unit_id` (förnoterad placering),
`ScanSession.inventory_session_id` (gruppering), `ScanSessionImage`
(extra bilder utöver OCR-målet).

### 8. Inventeringstillfälle som behållare

`InventorySession` grupperar skanningar gjorda i samma sammanhang.
En aktiv session i taget globalt - nya skanningar auto-länkas. Sessionen
har en append-only logg dit både systemet (vid varje skanning) och
användarna (manuella anteckningar) skriver tidsstämplade rader.

### 9. Multi-bilder per not + klientside-rotation

En not kan ha flera bilder (PieceImage med kind = cover/back/title_page/
inside/other). Den med lägst sort_order är primär (visas som thumbnail
i listor). Bilder kan läggas till på piece-edit-sidan eller redan
under skanningen via mobil quick-scan.

Rotation görs på två sätt:
- **Före upload**: client-side via HTML5 canvas i `quick.html` så
  användaren ser och rättar orienteringen innan filen lämnar enheten
- **Efter upload**: server-side via PIL `Image.rotate(expand=True)` på
  pieces/edit-sidan; thumbnailen regenereras

### 10. Person som entitet, inte text-fält

Kompositör, arrangör och textförfattare är inte text-fält på Piece
utan Person-entiteter länkade via PieceContributor (med roll).
Detta löser "Mendelssohn" vs "F. Mendelssohn" och möjliggör
"alla noter av X". `Piece.contributors_cache` är en denormaliserad
textsträng som indexeras av FTS5 för snabb sökning.

`replace_contributors`-helpern i `services/people.py` är det enda
stället där bidragslistorna sätts om - används av både scan-save,
piece-edit och nytt-piece-flödet. Den parsar `"X; Y & Z"`-strängar
och kör find-or-create per namn.

### 11. Dubblettkoll vid skanning

`services/duplicates.py::find_duplicates` körs när review_form öppnas.
Använder rapidfuzz på titel + partial_ratio på contributors_cache,
plus bonus +30 om edition_number matchar exakt. Förslag med
score >= 60% visas som varningsbanner med möjlighet att lägga till
placering på befintlig piece istället för att skapa ny.

För 200-1000 noter laddas alla pieces in i minnet vid varje sökning -
trivialt snabbt. Vid 10k+ behövs SQL-trigram (Postgres pg_trgm).

### 12. Avvisning av skanningar + retry

ScanSession.discarded (bool) markerar skanningar som granskaren
avvisat (suddiga bilder, dubbletter mm). Audit-spår bevaras via
discarded_at + discard_reason. Toggle "Visa avvisade" på /scan/queue
för att hitta tillbaka och återställa.

Misslyckade skanningar (Anthropic-fel, MB-fel) får en retry-knapp som
kö:ar om jobbet. _humanize_error() i ocr_jobs städar bort HTML-skräp
från Cloudflare-felsidor osv.

### 13. Inventeringsläge med per-enhet checklista

Inom en aktiv InventorySession kan användaren välja en storage_unit
och få en checklista över alla placeringar som ska finnas där.
Varje rad har snabbknappar ✓/⚠/✗ som skapar InventoryCheck-poster
(historik bevaras - inga unique constraints). HTMX uppdaterar bara
raden, statusen loggas automatiskt i sessionens fritext-logg.

### 14. CRUD för placeringar med sammanfogning

Placeringar kan läggas till, redigeras (inkl flyttas mellan units)
och tas bort från piece-detalj. Om edit byter unit till en där
samma piece redan har en placering så slås de samman (copies
summeras, gamla raden raderas) - idiotsäker mot dubbel-placeringar.

### 5. Sökning byggs runt körledarens mentalmodell

Körledaren söker typiskt efter *tillfälle*, inte titel. UI:t designas
för att stödja det:

- Primärfilter: liturgisk kategori (advent, jul, fasta, etc.)
- Sekundärfilter: besättning (SATB, SAB, SSA, unison, etc.)
- Tertiärfilter: svårighet, språk, tillfälle (begravning, bröllop, dop)
- Fritextsökning över titel, kompositör, anteckningar via FTS5

Liturgisk kategori är en tagg (många-till-många), inte ett kolumnvärde
- en not kan användas både i advent och i allmänna gudstjänster.

Sökningen är abstraherad bakom `services/search.py` med en
`SearchBackend`-protokoll, så att FTS5-implementationen kan bytas mot
en Postgres-baserad senare utan att route- eller template-kod ändras.

### 6. SQLite nu, Postgres-redo i koden

SQLite + SQLModel räcker för 200-1000 noter och några parallella
användare. Men koden ska skrivas så att övergången till Postgres är
mekanisk, inte arkitektonisk. Konkret:

- Använd SQLModel (Pydantic + SQLAlchemy) - samma modellkod fungerar
  på båda
- Lägg SQLite-specifik kod (FTS5, `WITHOUT ROWID`, etc.) bakom
  service-abstraktioner
- Datum/tidstämplar lagras alltid i UTC
- Använd inte SQLite-specifika typer eller funktioner i applikationskoden
  utan att markera det i `docs/postgres-migration.md`

Se `docs/postgres-migration.md` för fullständig portabilitetslista och
migrationsstrategi.

## Migrationsstrategi

**Före prod (nuvarande läge): nuke + seed.** Vid schemaändringar
raderar vi databasen och kör om seed-skriptet med testdata. Inga
ALTER-guards behövs. Detta sparar enormt med tid när modellen
fortfarande utvecklas. Se `docs/seed-data.md` för seed-strukturen
och CLI-kommandona.

**Efter prod**: `init_db()` med `SQLModel.metadata.create_all()` +
ALTER-guards för nya kolumner. Tröskeln är när användaren börjar
registrera skarp data och vi tar första riktiga backupen.

Aldrig backwards-compat-shims för data ingen annan än användaren har -
bara migrera och radera gammal kod.

## Miljövariabler

Se `.env.example` för aktuell mall. Centrala variabler:

```
APP_ENV=development
SESSION_SECRET=...                       # Slumpat, hex(32)
DATABASE_PATH=./data/notarkiv.db
IMAGES_PATH=./data/images

# OCR och berikning
OCR_PROVIDER=claude_vision                # claude_vision | tesseract | hybrid
ANTHROPIC_API_KEY=sk-ant-...              # Kan också sättas via /admin/settings
CLAUDE_MODEL=claude-haiku-4-5

# Task queue
REDIS_URL=redis://redis:6379/0

# MusicBrainz (kräver identifierande User-Agent enligt MB:s villkor)
MUSICBRAINZ_USER_AGENT=notarkiv/0.1 (din@email.tld)
MUSICBRAINZ_RATE_LIMIT_DELAY=1.0          # sekunder mellan anrop

# Observability
LOG_LEVEL=INFO
SENTRY_DSN=                               # valfri

# Bootstrap (skapas av db reset --seed om angiven)
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=byt-detta-direkt

# E-post (valfri, för lösenordsåterställning - kan läggas till senare)
# SMTP_HOST=...
# SMTP_PORT=587
# SMTP_USER=...
# SMTP_PASSWORD=...
# SMTP_FROM=notarkiv@dindoman.tld

# Backup (litestream)
LITESTREAM_BUCKET=...
LITESTREAM_ENDPOINT=...
LITESTREAM_ACCESS_KEY=...
LITESTREAM_SECRET_KEY=...
```

## Vanliga ändringar i framtiden

- **Nytt metadatafält**: lägg i `pieces`-tabellen via `ALTER TABLE`-guard
  i `init_db()`, uppdatera `pieces/edit.html`, lägg till i sökfiltret
  om relevant.
- **Ny OCR-provider**: skapa fil i `app/services/ocr/`, implementera
  `OCRProvider`-protocol, registrera i `routes/scan.py`.
- **Ny taggtyp**: utöka `kind`-enum i `tags`-tabellen, lägg till i
  taggvalsdropdown.

## Testning

Pytest + httpx async client + factory_boy är defaultverktyg.

- **Unit**: `services/ocr/` och `services/musicbrainz.py` med mockade
  bilder och API-svar
- **Integration**: route-tester med temp-SQLite (via `tmp_path`), riktig
  SQLModel session, factory_boy-fabriker för testdata
- **Regression**: `tests/fixtures/covers/` med 20+ representativa
  notomslag. Kör alla tre OCR-providers vid promptändring och jämför
  utdata. Använd `pytest --snapshot-update` för att uppdatera när
  förbättringar gjorts.
- **Manuell**: skanningsflöde måste testas i mobil webbläsare, inte bara
  via curl. Säg uttryckligen om mobiltest INTE är gjort.

## Vad det här projektet INTE är

Skydda omfattning - det är lätt att förvandla en "katalogapp" till en
hel körverksamhetsplattform. Följande är medvetet utanför scope:

- Utlåning/återlämning av exemplar (kan komma i v3)
- Framförandehistorik / vad-sjöng-vi-när
- Integration med körschema, Spotify, YouTube
- Notrendering eller -spelning (det är vad MuseScore är till för)
- Köp/inköpsförslag
- Användarrättigheter på not-nivå
- Versionshantering av arrangemang (om någon skrivit egna ändringar)

Om någon av dessa blir relevant, lägg in i ROADMAP först och diskutera.
