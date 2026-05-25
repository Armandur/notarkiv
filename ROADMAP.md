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
- [x] Redigeringsvy med metadata + bildhantering + MB-omsökning
- [x] Manuell skapelse av not utan skanning, med valfri placering
- [x] Borttagning av piece (admin, hård radering med cascade)
- [x] List- och kortvy via ?view= toggle
- [x] Tagghantering på piece (modal med pills, skapa ny tagg inline)
- [x] Manuell MB-sökning (egen söksträng, fritt redigerbar)

### Lagringsplatser

- [x] CRUD för `storage_locations` (fysisk/digital)
- [x] CRUD för `storage_units` med nestning
- [x] Trädvisning för administration
- [x] Lägga till/redigera/ta bort placeringar på en notpost
- [x] Flytta placering mellan enheter (med auto-sammanfogning vid kollision)
- [x] Visa full sökväg i dropdown och listor
- [x] UnitKind (typ av enhet) som autocomplete-entitet, dubletter blockerade

### Två-personers skanningsflöde

- [x] Mobil-anpassad snabbskanning (`/scan/quick`) med multi-bild,
      rotation och förhandsgranskning före upload
- [x] Granskningskö (`/scan/queue`) med thumbnails och status,
      kort- och list-vy
- [x] Pre-noterad placering på ScanSession som förifylls i granskning
- [x] Navbar-badge med antal väntande granskningar
- [x] Avvisa/återställ skanningar i kön (granskaren slipper kasta dåliga
      via vanlig CRUD)
- [x] "Spara och nästa i kön" i granskning för flytt mellan poster
- [x] Dubblettkoll vid granskning - föreslå "lägg till placering"
      istället för ny piece

### Inventeringstillfälle

- [x] InventorySession-modell med planerad plats, beskrivning, logg
- [x] Lista, skapa, detalj, avsluta sessioner
- [x] Auto-länkning av skanningar till aktiv session
- [x] Aktivitetslogg med tidsstämplar (auto + manuell)
- [x] Navbar-prick när session är aktiv
- [x] Inventeringsläge: per-enhet checklista med ✓/⚠/✗-knappar,
      InventoryCheck-poster för historik, progress-bar

### Admin

- [x] Användarhantering: lista, skapa (auto-genererat lösenord), byt roll,
      återställ lösenord, ta bort
- [x] Inställningar: Anthropic API-nyckel, Claude-modell, OCR-default,
      MusicBrainz User-Agent
- [x] AppSetting-tabell med env-fallback - ändringar utan omstart

### Backup

- [x] `scripts/backup.sh` - sqlite3 .backup + gzip + rclone copyto för DB,
      rclone sync för bilder. Konfigurerat mot Google Drive via rclone.
- [x] `scripts/restore.sh` - hämtar senaste eller specifik snapshot
- [x] Dokumenterad i `docs/backup.md` med engångsuppsättning och
      cron-exempel
- [ ] Verifierad återställningskörning (kvar tills användaren testat)

### Klart-kriterier för MVP

- Användaren kan skanna in 50 noter på en kvällsstund utan friktion
- Hela arbetslaget kan logga in, söka och se var noter finns
- Backupen fungerar (manuellt verifierad återställning)

## V2 - "Riktigt användbart"

- [x] **Kompositörer/personer som egna entiteter**: Person-tabell med
      sort_name, biografi, MB-artist-MBID, Wikipedia-länk.
      PieceContributor-länkning med roll (composer/arranger/lyricist).

### Förbättringar kring Person

- [x] **Person-autocomplete med levnadsår och antal noter**: datalist
      under composer/arranger/lyricist visar nu "Namn · 1809-1847 · 3
      noter" som extra info (Chrome/Firefox stöder option-label).
      Riktig HTMX-dropdown med "Skapa ny"-knapp kvarstår.
- [x] **Justerbart sort_name vid skapande/redigering**: under varje
      composer/arranger/lyricist-fält finns nu en sort-namn-input
      som auto-uppdateras från huvudfältet via JS men kan rättas
      manuellt. Backend stödjer override via parse_sort_field +
      replace_contributors. Förifylld från Person.sort_name i edit-vyn.
- [ ] **Auto-import av Person från MusicBrainz**: arq-jobb finns
      (enrich_person_job) som söker MB, accepterar match med fuzz-score
      >= 88 och berikar in-place. Triggas dock inte automatiskt - nuvarande
      design är att användaren aktivt väljer applicering via MB-förslag
      i granskningsflödet.
- [ ] **Auto-crop med jscanify**: webbläsare-baserad dokument-detektion
      via OpenCV.js, perspektivkorrigering, manuell hörnjustering.
      Ersätter standard `<input capture>` på mobil.
- [ ] **Dokumentfilter likt OneDrive-skannern**: efter crop, klientside-
      filter för gråskala, svartvit (adaptiv tröskel), nivåjustering
      och skärpa. Användaren väljer per skanning vilken filtertyp.
      Hör ihop med auto-crop och OpenCV.js.
- [ ] **Batch-skanningsläge**: skanna in flera *noter* i rad utan att gå
      tillbaka mellan varje (jfr nuvarande multi-foto som gäller samma not)
- [x] **Dubblettkoll**: vid skanning, jämför mot befintliga poster på
      `(titel, kompositör, arrangör)`. Föreslå "lägg till placering"
      istället för "skapa ny post" om träff finns
- [x] **Inventeringsläge**: visa allt som ska ligga i en specifik
      storage_unit, checkbar lista, markera "saknas"
- [x] **Flytt- och omorganisationshantering (grundläggande)**: redigera
      befintliga placeringar (byt enhet eller antal exemplar) med
      auto-sammanfogning. Kvar: split av placering ("5 ex stannar här,
      10 ex flyttas dit"), bulk-flytt av allt från en enhet, audit-spår
      med PlacementEvent-tabell.
- [ ] **PDF-katalog**: exportera hela eller filtrerad lista som PDF
      för utskrift (körpärm, allmän översikt)
- [x] **QR-kod på storage_units**: utskrivbara etiketter, varje QR
      pekar på enhetens detaljvy. Webbläsarens kamera räcker.
- [x] **Foton på storage_units**: StorageUnitImage-tabell (samma mönster
      som PieceImage). Uppladdning, etikett, rotation och radering på
      enhetens detaljvy. Cascade-delete från unit till bilder via FK,
      bildfiler raderas från disk vid borttagning. Stöd för flera bilder
      per enhet (framsida + rygg t.ex.).

### V2-utvidgning av QR-flödet

- [ ] **In-browser QR-läsare**: live kameravy direkt i webbappen
      (getUserMedia + jsQR eller html5-qrcode-biblioteket) så
      användaren slipper växla mellan kameraappen och webbläsaren.
      Med detta blir det också naturligt att låta QR-koderna kodifiera
      en stabil UUID istället för full URL - då går etiketterna inte
      sönder om appen flyttar till annan domän. Krav: live-detection
      som funkar smidigt på mobil (testning behövs).
- [x] **Anteckningsfält per användare på not** (körledarens egna
      tonarter, repetitionsnoter etc.)
- [x] **Utlåningshantering**: registrera utlån per placering med
      låntagare, antal, ev. förv. retur. Återlämning markeras med
      knapp. Global /loans-sida visar aktiva utlån. Navbar-badge.
- [x] **Förbättra utlåningens borrower-fält**: dropdown med systemets
      användare som default (förinifylld på inloggad). Bockruta
      "Extern person" kopplar bort dropdownen och visar fritext-input
      för vikarierande körledare etc. borrower_user_id sätts vid
      användarval, annars sparas borrower_name som fritext.
- [x] **MB-berikning av personer direkt i granskningsflödet**: en samlad
      "Hämta MB-förslag"-knapp i review-vyn som söker för både verk och
      alla namn (composer/arranger/lyricist), visar topp-träffar med
      sammanfattning (levnadsår, land, MBID) och låter användaren
      aktivt klicka "Använd" per förslag. Verkförslag sätter MBID/titel
      via JS; personförslag skapar/uppdaterar Person direkt med
      enrich_person_from_mb + Wikipedia + porträtt via HTMX-POST.
- [x] **Land som flagga + fullt namn på personer**: utbytt mot
      🇩🇪 Tyskland-formatering via app/utils/countries.py (svensk
      lookup-tabell för ~50 länder).
- [x] **Filter och sökning på personer**: roll-, land- och
      MBID-status-dropdown plus fritextsökning på namn.
- [ ] **Streckkod/QR-etiketter på enskilda noter + kioskvy**:
      Idag har vi QR per lagringsenhet. Komplement: en etikett per piece
      med en stabil kod (UUID eller löpnummer) som kan skannas med USB-
      handskanner eller mobilens kamera. Användningsfall:
      - Terminaldator i notförrådet med kioskvy: skanna -> piece-detalj
        med snabb "Låna"/"Återlämna"-knapp (inloggad användare som
        låntagare som default)
      - Inventera genom att bara skanna varje not i en pärm - systemet
        markerar som hittad automatiskt
      USB-handskannrar fungerar som tangentbordsemulering så de skriver
      koden i ett input-fält direkt. För kamera-baserad skanning krävs
      HTTPS (kopplat till in-browser-QR-läsare-itemet ovan).
- [ ] **Psalmnummer som strukturerad tagg-referens istället för fritext-fält**:
      idag är `Piece.psalm_number` en enkel int. Bättre design:
      taggar/referenser i formen `Svenska Psalmboken:1986:246` eller
      `Psalmer i 2000-talet:NN` så samma not kan referera till flera
      psalmböcker/utgåvor. Behöver: dropdown för psalmbok (kuraterad
      lista), utgåva-fält, nummer-fält. Migrering av befintliga
      psalm_number → tagg-rad med användarens default-psalmbok.
- [ ] **Besättning som strukturerad entitet/taggar istället för fritext**:
      idag är `Piece.voicing` ett fritextfält ("SATB", "SAB" etc.) vilket ger
      stavvarianter och inkonsekvens. Alternativ att diskutera: (a) enum med
      fast lista (SATB, SAB, SSA, SSAA, TTBB, unison, kanon ...), (b) tagg-
      kind "voicing" med autocomplete + förvalda alternativ, (c) två fält
      (typ + antal stämmor). Påverkar filter, sökning och OCR-extraktion.
- [x] **Sök och sortera /pieces på inläggningsdatum**:
      sort-dropdown (nyast/äldst/titel A-Ö/Ö-A) + period-dropdown
      (alltid/7/30/90 dagar).
- [x] **Senaste noter på startsidan**: 8 senast inlagda som
      thumbnail-grid ovanför stats-korten.

## V3 - "Långt fram"

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

- URL-fält på digitala storage_units (sökväg via nästlade enheter räcker,
  t.ex. "Teams › Musikerteamet › Notermappen")
- Hård integration mot SharePoint/Teams
- Notrendering eller -spelning (det är vad MuseScore är till för)
- Köp/inköpsförslag eller budgethantering
- Versionshantering av arrangemang
- Per-not användarrättigheter
