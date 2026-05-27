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

## Köra dev-servern (Claude startar servern under utveckling)

Under utveckling är det **Claude** som startar och stänger ner uvicorn
och arq-worker - inte användaren via tmux. Det innebär att jag som agent
måste komma ihåg att starta båda processer och hålla koll på dem.

### Standardprocedur

1. **Verifiera Redis** (kör i bakgrunden, oftast redan igång på 6379)
2. **Starta uvicorn på 8766** med reload + dev.log som outputfil
3. **Starta arq-worker** i bakgrunden mot samma Redis

```bash
# Kolla att Redis lyssnar
ss -tlnp 2>/dev/null | grep 6379

# Stoppa ev. gamla processer först
pkill -f "uvicorn app.main"
pkill -f "arq app.tasks.worker"

# Starta uvicorn (run_in_background: true via Bash-verktyget)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8766 --reload > dev.log 2>&1

# Starta arq-worker (run_in_background: true)
uv run arq app.tasks.worker.WorkerSettings > worker.log 2>&1
```

### Verifiering

```bash
# Båda processer ska finnas
pgrep -af "uvicorn app.main"
pgrep -af "arq.*WorkerSettings"

# Servern svarar
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8766/login

# Workern loggar "Starting worker for 2 functions"
grep -i "starting worker" worker.log

# Smoke som inloggad
uv run python scripts/smoke.py
```

### Tystna fel

- **OCR/MB hänger på "väntar på worker"** → arq-worker körs inte. Starta den.
- **`/loans/cart` ger 500 med kolumn-fel** → init_db har inte kört. Restart uvicorn.
- **`Address already in use`** → en gammal uvicorn-process hänger. `pkill -f "uvicorn app.main"` först.
- **OCR-jobb failar med `ScanSession N saknas`** → flera arq-workers konkurrerar om samma queue och en gammal har stale SQLite-connection. Döda ALLA befintliga workers (`pkill -f "arq.*WorkerSettings"`) innan ny startas - en worker i taget räcker.

### Filloggar

- `dev.log` - uvicorn-output (requests, tracebacks)
- `worker.log` - arq-output (jobb-utförande, fel)

Båda är gitignored (via `*.log` i `.gitignore`).

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
      piece.py              # Piece + CopyrightStatus
      piece_image.py        # PieceImage (flera bilder per not)
      piece_user_note.py    # Personliga anteckningar per användare på en not
      person.py             # Person + PersonLink + PieceContributor + ContributorRole
      storage.py            # StorageLocation, StorageUnit, PiecePlacement, UnitKind
      storage_unit_image.py # Bilder på lagringsenheter (skanna ryggen på pärmen)
      tag.py                # Tag, PieceTag (besättning + ackompanjemang är taggar)
      scan_session.py       # ScanSession + ScanStatus
      scan_session_image.py # Extra bilder per skanning
      inventory.py          # InventorySession
      inventory_check.py    # InventoryCheck (en check per placering inom session)
      loan.py               # Loan (kan vara fristående eller batch_id-länkad)
      loan_batch.py         # LoanBatch + LoanBatchStatus (cart/picking/active/returned)
      psalm.py              # PsalmBook, PsalmEntry, PiecePsalmRef
      app_setting.py        # Key-value-runtime-inställningar
    routes/
      pages.py              # GET / med översikt (sort + senaste noter)
      auth.py               # login/logout/change-password
      pieces.py             # CRUD, multi-bildhantering, MB-omsökning, taggar, placeringar, PDF-utskrift, psalmreferenser
      people.py             # Lista, detalj med biografi (Wikipedia) + portrait (MB/Wikidata) + länkar
      scan.py               # /scan (vanlig), /scan/quick (mobil), /scan/queue, /scan/{id}/*
                            # /scan/{id}/discard|restore för avvisning, manuell MB-sökning
      storage.py            # CRUD för locations, units, unit-kinds (autocomplete)
      inventory.py          # CRUD för inventeringstillfällen + logg + per-enhet check-läge
      loans.py              # Enskilda lån + LoanBatch-flöde (cart/checkout/pickup/return) + PDF-plocklista
      tags.py               # Översikt + admin för taggar
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
      base.html             # Navbar med badges (kö/utlån/korg), lightbox (med grupp-nav), confirm-modal
      auth/                 # login.html, change_password.html
      pages/                # index.html (översikt + senaste noter)
      pieces/
        list.html           # Kort/list/träd-vy via ?view=, multi-select-filter
        detail.html         # Read-orienterad: metadata, galleri, placeringar, taggar
        edit.html           # Metadata + bilder + MB-sökning (allt redigerbart, EasyMDE för anteckningar)
        new.html            # Manuell skapelse med valfri placering
        pdf.html            # WeasyPrint-mall för utskrift av notkatalogen
        _musicbrainz_modal.html  # HTMX-modal med söksformulär + förslag
        _tag_area.html, _tag_search_results.html  # HTMX-driven tagghantering
      scan/
        capture.html        # Vanlig (dator) skanningssida
        quick.html          # Mobil snabbskanning, multi-bild + rotation
        processing.html     # HTMX-pollad statussida efter upload
        review.html         # Granskningsformulär (OCR + MB + dubbletter + "spara och nästa")
        queue.html          # Granskningskö (kort/list-toggle, avvisa/återställ)
        _status.html        # HTMX-fragment
        _musicbrainz_modal.html  # Manuell MB-sökning från granskning
      people/               # list.html, detail.html (biografi-markdown + lightbox-portrait)
      storage/
        manage.html, _tree.html, _unit_form.html, _kind_results.html
      inventory/
        list.html, detail.html
        check_pick.html, check.html, _check_row.html  # Per-enhet inventeringsläge
      loans/
        list.html           # Batches grupperade + enskilda lån
        cart.html           # Utlåningskorg per användare (plats-grupperad)
        pickup.html         # Plockläge med ✓ Hämtad / ✗ Hittade ej per rad
        batch_detail.html   # Detalj-vy med delvis återlämning
        pickup_pdf.html     # Utskrivbar plocklista (WeasyPrint)
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
    psalms/                 # 1986 + 2003 års svenska psalmbok som referensdata
    (users.yaml, storage_locations.yaml - läggs till av användaren)
  snapshots/                # Lokala DB+image-snapshots (gitignored)
  docs/
    datamodell.md, ocr-strategi.md, musicbrainz.md
    postgres-migration.md, seed-data.md, backup.md
    musikerbeskrivning.md   # Strategi för Wikipedia-biografi + Wikidata-portraits
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

### 15. Bulk-utlån via LoanBatch och kundvagn

Enskilda `Loan`-rader finns kvar (gamla flödet via piece-detalj-modalen)
men det normala är nu **grupperade utlån** via `LoanBatch`. Statusfältet
driver livscykeln: `cart → picking → active → returned`.

- **cart**: en per användare. Loan-rader läggs till med null borrower
  och null picked_up_at. Navbar visar "Korg (N)".
- **checkout** flippar till **picking** och kräver obligatoriskt
  syfte/namn (t.ex. "Konsert 14 juni") + låntagare + ev. förv. retur.
- **picking** är plockläget: per rad ✓ Hämtad (sätter `picked_up_at`)
  eller ✗ Hittade ej (raderar raden). Användaren kan lägga till fler
  noter mitt under plockning. PDF-plocklista finns för fysisk avbockning.
- **active** sätts när användaren slutregistrerar. Ohämtade rader
  raderas helt. Återlämning kan göras per rad (delvis) eller helt batch.
- **returned** sätts automatiskt när alla rader är återlämnade.

`Loan.batch_id = null` betyder enskilt lån (gamla flödet) - de räknas
direkt som hämtade (`picked_up_at` sätts vid registrering).

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

**Default: ALTER TABLE-guards via `_ensure_column_guards()` i `app/db.py`.**
`init_db()` kör `SQLModel.metadata.create_all()` (skapar nya tabeller
som saknas) följt av en additions-dict per tabell med (kolumn, definition)-
par. För varje par: kolla `PRAGMA table_info(table)` och kör `ALTER TABLE
... ADD COLUMN ...` om kolumnen saknas. Idempotent och säkert på riktig data.

**Aldrig `db reset` på en databas som har skarp data utan att fråga
användaren först.** Det finns alltid en risk att det som ser ut som
testdata egentligen är registrerat material (skannade noter, biografier,
manuell metadata) - data minimization-principen gör att backupen kanske
inte ens täcker allt mellan snapshots. Lokala snapshots ligger i
`snapshots/` (gitignored) och kan användas för katastrofåterställning.

Seed (`seed_all()`) körs alltid efter `init_db()` och är idempotent.
Den fyller på baseline-data:
- Taggar (`seed_data/tags.yaml`) inkl. besättning + ackompanjemang
- UnitKinds (`seed_data/unit_kinds.yaml`)
- Initial admin (om `INITIAL_ADMIN_*` är satt i env)
- Psalmböcker (`seed_data/psalms/*.yaml`) - 1986 + 2003 års psalmbok

Schemaändringar du gör i en modell **måste** alltså antingen:
1. Bara skapa en ny tabell (då räcker `create_all()` automatiskt), eller
2. Bara lägga till en kolumn på befintlig tabell (lägg in i `additions`-
   dicten i `_ensure_column_guards()`).

Andra ändringar (rename, drop, type change) kräver manuell SQL eller
om-skanning av data. Diskutera med användaren först.

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

- **Nytt metadatafält** på en befintlig tabell: lägg till i SQLModel,
  lägg in `(kolumn, "SQL_TYPE")` i `_ensure_column_guards()` i
  `app/db.py`, uppdatera relevanta templates, lägg till i sökfiltret
  om relevant.
- **Helt ny tabell**: skapa modell, importera i `app/models/__init__.py`
  och `app/db.py` (för att triggra `create_all()`). Inga ALTER-guards
  behövs - tabellen skapas automatiskt vid nästa start.
- **Ny OCR-provider**: skapa fil i `app/services/ocr/`, implementera
  `OCRProvider`-protocol, registrera i `routes/scan.py`.
- **Ny taggtyp**: utöka `kind`-enum i `tags`-tabellen, lägg till i
  taggvalsdropdown.
- **Ny seed-data**: lägg yaml-fil i `seed_data/`, lägg en `_seed_X()`-
  funktion i `app/seed.py` och anropa från `seed_all()`. Idempotent
  via SELECT-före-INSERT.

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
