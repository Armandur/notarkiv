# Roadmap

Levande dokument. Uppdateras i dialog mellan användaren och Claude när
omfattning ändras.

## MVP - "Kan ersätta dagens kaos"

Målet med MVP:n är att verifiera grundflödet: skanna -> extrahera ->
granska -> spara -> hitta igen.

### Backend och infra

- [x] FastAPI-skelett med lifespan, settings, statiska filer, templates
- [x] SQLModel-modeller enligt `docs/datamodell.md`
- [x] `init_db()` med `metadata.create_all()` + FTS5-triggers
- [x] CLI (`app/cli.py`): `db init/reset/seed`, `users create/create-admin/reset-password`
- [x] Seed-skript som läser YAML från `seed_data/`, idempotent
- [x] Loguru-konfiguration, valfri Sentry-init via env-variabel
- [x] Användarhantering: User-modell, bcrypt-hashning, login/logout-flöde
- [x] Roller: `reader`, `editor`, `admin`
- [x] Initial admin-bootstrap via env-variabel
- [x] arq-worker med Redis
- [x] Docker + docker-compose med tre tjänster

### Skanningsflöde

- [x] Mobilanpassad uppladdningssida med kamerakomponent
- [x] OCR-abstraktion (`OCRProvider`-protocol)
- [x] Claude Vision-implementation (default)
- [x] Tesseract-implementation (fallback)
- [ ] Hybrid-implementation (Tesseract OCR + Claude för strukturering)
      - Fallar tillbaka till claude_vision tills implementerad
- [x] arq-job som kör OCR i bakgrunden, returnerar omedelbart
- [x] HTMX-pollad jobbstatus med progress
- [x] Granskningsformulär med förifyllda fält och möjlighet att rätta
- [x] Bildlagring (originalbild + thumbnail)
- [x] Multi-bilder per not (PieceImage) - lägg till efter skanning eller
      under quick-scan
- [x] Klientside-rotation i mobil quick-scan + serverside-rotation på
      detaljvy

### MusicBrainz-berikning

- [x] `services/musicbrainz.py` med rate-limited klient (1 req/sek)
- [x] In-memory LRU-cache för sökningar
- [x] arq-job som triggas efter OCR-extraktion
- [x] UI i granskningsformuläret: förslag med "Använd"-knapp

### Notpost-hantering

- [x] Listvy med sökning (FTS5 på titel/kompositör/anteckningar)
- [x] Detaljvy med all metadata och placeringar
- [ ] Redigeringsvy (utöver via review-formuläret från skanning)
- [ ] Borttagning av piece

### Lagringsplatser

- [x] CRUD för `storage_locations` (fysisk/digital)
- [x] CRUD för `storage_units` med nestning
- [x] Trädvisning för administration
- [x] Lägga till placeringar på en notpost via review
- [x] Visa full sökväg i dropdown och listor
- [x] UnitKind (typ av enhet) som autocomplete-entitet, dubletter blockerade

### Två-personers skanningsflöde

- [x] Mobil-anpassad snabbskanning (`/scan/quick`) med multi-bild,
      rotation och förhandsgranskning före upload
- [x] Granskningskö (`/scan/queue`) med thumbnails och status
- [x] Pre-noterad placering på ScanSession som förifylls i granskning
- [x] Navbar-badge med antal väntande granskningar

### Inventeringstillfälle

- [x] InventorySession-modell med planerad plats, beskrivning, logg
- [x] Lista, skapa, detalj, avsluta sessioner
- [x] Auto-länkning av skanningar till aktiv session
- [x] Aktivitetslogg med tidsstämplar (auto + manuell)
- [x] Navbar-prick när session är aktiv

### Admin

- [x] Användarhantering: lista, skapa (auto-genererat lösenord), byt roll,
      återställ lösenord, ta bort
- [x] Inställningar: Anthropic API-nyckel, Claude-modell, OCR-default,
      MusicBrainz User-Agent
- [x] AppSetting-tabell med env-fallback - ändringar utan omstart

### Backup

- [ ] `litestream` konfigurerat mot offsite-bucket
- [ ] Nattlig cron för bilduppladdningsmapp (rsync till samma bucket)
- [ ] Dokumenterad återställningsprocess

### Klart-kriterier för MVP

- Användaren kan skanna in 50 noter på en kvällsstund utan friktion
- Hela arbetslaget kan logga in, söka och se var noter finns
- Backupen fungerar (manuellt verifierad återställning)

## V2 - "Riktigt användbart"

- [x] **Kompositörer/personer som egna entiteter**: Person-tabell med
      sort_name, biografi, MB-artist-MBID, Wikipedia-länk.
      PieceContributor-länkning med roll (composer/arranger/lyricist).

### Förbättringar kring Person

- [ ] **Person-autocomplete i granskningsformulär**: ersätt fritextfälten
      med autocomplete-fält likt UnitKind, med "Skapa ny: '<namn>'"-knapp
      vid ingen träff. Visar levnadsår och antal noter i förslagslistan.
- [ ] **Auto-import av Person från MusicBrainz**: när MB-träff används,
      slå upp artist via MB:s artist-rels och url-rels för att hämta
      kanoniskt namn, MBID, Wikipedia-URL och levnadsår. Skapar
      Person-poster med all metadata förifylld.
- [ ] **Auto-crop med jscanify**: webbläsare-baserad dokument-detektion
      via OpenCV.js, perspektivkorrigering, manuell hörnjustering.
      Ersätter standard `<input capture>` på mobil.
- [ ] **Dokumentfilter likt OneDrive-skannern**: efter crop, klientside-
      filter för gråskala, svartvit (adaptiv tröskel), nivåjustering
      och skärpa. Användaren väljer per skanning vilken filtertyp.
      Hör ihop med auto-crop och OpenCV.js.
- [ ] **Batch-skanningsläge**: skanna in flera *noter* i rad utan att gå
      tillbaka mellan varje (jfr nuvarande multi-foto som gäller samma not)
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
      gudstjänster/konserter, generera statistik
- [ ] **Offline-stöd via PWA**: cacha skanningar lokalt på mobilen och
      synca när uppkoppling finns
- [ ] **IMSLP-integration** för fri sheet music där det finns
      (kompletterar MusicBrainz)
- [ ] **Filuppladdning** för digitala noter (PDF, MusicXML, MP3) om
      behovet visar sig
- [ ] **MS Graph API-integration** för att bläddra SharePoint direkt
- [ ] **Postgres-migration**: när skalan eller behovet av bättre
      fuzzy-search motiverar det

## Aktivt utanför scope

- Hård integration mot SharePoint/Teams (bara URL räcker)
- Notrendering eller -spelning (det är vad MuseScore är till för)
- Köp/inköpsförslag eller budgethantering
- Versionshantering av arrangemang
- Per-not användarrättigheter
