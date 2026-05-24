# Roadmap

Levande dokument. Uppdateras i dialog mellan användaren och Claude när
omfattning ändras.

## MVP - "Kan ersätta dagens kaos"

Målet med MVP:n är att verifiera grundflödet: skanna -> extrahera ->
granska -> spara -> hitta igen. Allt annat är tillägg.

### Backend och infra

- [ ] FastAPI-skelett med lifespan, settings, statiska filer, templates
- [ ] SQLModel-modeller enligt `docs/datamodell.md`
- [ ] `init_db()` med `metadata.create_all()` (ALTER-guards läggs till
      vid prod-tröskeln, se `docs/seed-data.md`)
- [ ] CLI (`app/cli.py`): `db reset`, `db seed`, `users create-admin`
- [ ] Seed-skript som läser YAML från `seed_data/`, idempotent där
      meningsfullt (se `docs/seed-data.md`)
- [ ] Loguru-konfiguration, valfri Sentry-init via env-variabel
- [ ] Användarhantering: User-modell, bcrypt-hashning, login/logout-flöde
- [ ] Roller: `reader`, `editor`, `admin`
- [ ] Initial admin-bootstrap via env-variabel eller CLI-kommando
- [ ] arq-worker med Redis, registrera grundläggande jobs
- [ ] Docker + docker-compose med tre tjänster (app, worker, redis),
      volym-mappad SQLite och bildmapp

### Skanningsflöde

- [ ] Mobilanpassad uppladdningssida med kamerakomponent (minimal vanilla JS)
- [ ] OCR-abstraktion (`OCRProvider`-protocol)
- [ ] Claude Vision-implementation (default)
- [ ] Tesseract-implementation (fallback)
- [ ] Hybrid-implementation (Tesseract OCR + Claude för strukturering)
- [ ] arq-job som kör OCR i bakgrunden, returnerar omedelbart med job-ID
- [ ] HTMX-pollad jobbstatus med progress
- [ ] Granskningsformulär med förifyllda fält och möjlighet att rätta
- [ ] Bildlagring (originalbild + thumbnail)

### MusicBrainz-berikning

- [ ] `services/musicbrainz.py` med rate-limited klient (1 req/sek)
- [ ] Lokal cache (sqlite-baserad eller in-memory) för att undvika
      upprepade lookups
- [ ] arq-job som triggas efter OCR-extraktion, gör MB-lookup på
      `(title, composer)` och bifogar förslag till granskningsformuläret
- [ ] UI i granskningsformuläret: "Möjlig matchning: ... Använd?"

### Notpost-hantering

- [ ] Listvy med sökning och filter (FTS5 på titel/kompositör/anteckningar)
- [ ] Detaljvy med all metadata och placeringar
- [ ] Redigeringsvy
- [ ] Borttagning (med bekräftelse, soft delete eller hard delete -
      bestäms vid implementation)

### Lagringsplatser

- [ ] CRUD för `storage_locations` (fysisk/digital)
- [ ] CRUD för `storage_units` med nestning
- [ ] Trädvisning för administration
- [ ] Lägga till/ta bort placeringar på en notpost
- [ ] Visa full sökväg i dropdown och listor

### Backup

- [ ] `litestream` konfigurerat mot offsite-bucket
- [ ] Nattlig cron för bilduppladdningsmapp (rsync till samma bucket)
- [ ] Dokumenterad återställningsprocess

### Två-personers skanningsflöde

- [ ] Mobil-anpassad snabbskanning (`/scan/quick`): kamera + valfri
      placering, sparar utan att tvinga granskning, retur till kamera
      direkt
- [ ] Granskningskö (`/scan/queue`): lista över skanningar som väntar
      på granskning, med thumbnail, status och ev. för-noterad placering
- [ ] Pre-noterad placering på ScanSession (placement_unit_id +
      placement_copies) som förifylls i granskningsformuläret
- [ ] Navbar-badge med antal väntande granskningar

### Klart-kriterier för MVP

- Användaren kan skanna in 50 noter på en kvällsstund utan friktion
- Hela arbetslaget kan logga in, söka och se var noter finns
- Backupen fungerar (manuellt verifierad återställning)

## V2 - "Riktigt användbart"

- [ ] **Kompositörer/personer som egna entiteter**: Person-tabell med
      sort_name, biografi, MB-artist-MBID, Wikipedia-länk.
      PieceContributor-länkning med roll (composer/arranger/lyricist).
      Auto-import via MusicBrainz när MB-träff finns.
- [ ] **Auto-crop med jscanify**: webbläsare-baserad dokument-detektion
      via OpenCV.js, perspektivkorrigering, manuell hörnjustering.
      Ersätter standard `<input capture>` på mobil.
- [ ] **Batch-skanningsläge**: skanna in flera noter i rad utan att gå
      tillbaka mellan varje
- [ ] **Dubblettkoll**: vid skanning, jämför mot befintliga poster på
      `(titel, kompositör, arrangör)`. Föreslå "lägg till placering"
      istället för "skapa ny post" om träff finns
- [ ] **Inventeringsläge**: visa allt som ska ligga i en specifik
      storage_unit, checkbar lista, markera "saknas"
- [ ] **PDF-katalog**: exportera hela eller filtrerad lista som PDF
      för utskrift (körpärm, allmän översikt)
- [ ] **Trasiga URL-detektor**: nattlig HEAD-kontroll på digitala
      placeringars URL:er, flagga 404/redirects
- [ ] **QR-kod på storage_units**: generera utskrivbara QR-koder som
      länkar till respektive enhet
- [ ] **Anteckningsfält per användare på not** (t.ex. körledarens egna
      tonarter, framförandetempo)

## V3 - "Långt fram"

- [ ] **Utlåningshantering**: "körledare X har lånat 5 ex av Y till och
      med datum Z"
- [ ] **Framförandehistorik**: spara vilka noter som använts vid vilka
      gudstjänster/konserter, generera statistik ("vad sjöng vi på
      advent senast?")
- [ ] **Offline-stöd via PWA**: cacha skanningar lokalt på mobilen och
      synca när uppkoppling finns
- [ ] **IMSLP-integration** för fri sheet music där det finns
      (kompletterar MusicBrainz)
- [ ] **Filuppladdning** för digitala noter (PDF, MusicXML, MP3) om
      behovet visar sig
- [ ] **MS Graph API-integration** för att bläddra SharePoint direkt
      från placeringsformuläret
- [ ] **Postgres-migration**: när skalan eller behovet av bättre
      fuzzy-search motiverar det. Se `docs/postgres-migration.md`.

## Aktivt utanför scope

Saker som diskuterats och avfärdats för att hålla projektet fokuserat:

- Hård integration mot SharePoint/Teams (bara URL räcker, bekräftat
  2026-05-24)
- Notrendering eller -spelning (det är vad MuseScore är till för)
- Köp/inköpsförslag eller budgethantering
- Versionshantering av arrangemang
- Per-not användarrättigheter
