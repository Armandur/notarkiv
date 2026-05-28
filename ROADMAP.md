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
- [ ] **Interaktiv approval av låg-konfidens-personmatchningar**:
      `enrich_person_job` auto-triggas redan från scan-save, piece-update
      och manuell-create via `enqueue_enrich_for_piece`. Auto-applicerar
      bara träff med fuzz-score >= 88. Lägre konfidens lämnas oberikade
      utan spår. Förslag: spara kandidater under tröskeln på Person så
      personens detaljsida kan visa "Möjliga MB-matchningar" med
      Använd-knapp per kandidat. Plus retry-knapp för jobb som
      misslyckades. Kräver ny PersonCandidate-tabell eller JSON-fält på
      Person.
- [x] **Auto-crop med jscanify** (commit 5e1dfa1, 54118cf): webbläsare-
      baserad dokument-detektion via OpenCV.js + jscanify, körs direkt
      efter att bilden tas. Cropad version blir thumbnail med "cropad"-
      badge. ⊡-knapp öppnar modal med dragbara hörn för manuell
      justering, original-bilden bevaras för re-cropping. Magnifier
      med hårkors vid dragning. Bukhari/Shafait-formel för korrekt
      aspekt-beräkning från perspektivprojicerade hörn. Förhandsgranskning
      i fullskärm med kontroller (cropa, rotera, filter).
- [x] **Dokumentfilter** (commit ea17a2e, c4a4005, senare iterationer):
      klientside via OpenCV.js. Default = Original (filter appliceras
      inte automatiskt eftersom flera testpipelines visat sig vara
      försämringar i olika fall). "Dokument"-knappen kör nu per-RGB-
      kanal auto-levels (stretch 0.5/99.5-percentilen till 0/255) som
      tar bort färgcast (gulnade papper blir vita) utan att förstöra
      färgglada omslag. Övriga knappar: Original / Svartvit (adaptiv
      tröskel) / Gråskala. Cache per (source, filter) → blob så växling
      mellan filter blir omedelbar.
- [ ] **Permanent radering av skanningar**: idag finns bara "Avvisa"
      (mjuk-radering via `discarded`-flag) i granskningskön - de blir
      kvar i DB:n och syns när "Visa avvisade" är på. För att städa
      ordentligt behövs en "Ta bort permanent"-åtgärd som även raderar
      bildfiler från `data/images/`. Lägg som admin-bara-knapp i scan-
      detalj-vyn för avvisade skanningar.
- [ ] **Klick-på-vit kalibrering**: lägg en "Vitbalans"-knapp i preview-
      modalen som låter användaren klicka på en punkt i bilden som ska
      vara vit. Beräkna offset per RGB-kanal (255 - clicked_value) och
      applicera på hela bilden. Bra för noter med starkt färgcast som
      auto-levels inte fångar. Kräver canvas-click-handler ovanpå
      preview-bilden, översättning mellan canvas-koordinater och bild-
      koordinater. UX-tungt på mobil men kan ändå vara värdefullt för
      svåra fall.
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
- [x] **PDF-katalog**: /pieces/print.pdf-endpoint som genererar PDF
      via WeasyPrint. Samma filter-params som /pieces (sök, taggar,
      voicing, språk, plats). Tabell-layout med titel/komp/besättning/
      språk/förlag/placering. Sidnummer i sidfot via @page CSS.
      "PDF"-knapp i /pieces toppen bredvid "Skriv ut".
- [x] **QR-kod på storage_units**: utskrivbara etiketter, varje QR
      pekar på enhetens detaljvy. Webbläsarens kamera räcker.
- [x] **Foton på storage_units**: StorageUnitImage-tabell (samma mönster
      som PieceImage). Uppladdning, etikett, rotation och radering på
      enhetens detaljvy. Cascade-delete från unit till bilder via FK,
      bildfiler raderas från disk vid borttagning. Stöd för flera bilder
      per enhet (framsida + rygg t.ex.).

### V2-utvidgning av QR-flödet

- [x] **In-browser QR-läsare** (commit 57b0b47): navbar-knapp "📷 Sök QR"
      öppnar Bootstrap-modal med live-kameravy via html5-qrcode-biblioteket
      (laddas som statisk fil under /static/js/ för CSP/proxy-säkerhet).
      Parsar piece-URL, kiosk-URL och rent public_id - navigerar till
      /p/<id> som auto-routar till piece-detalj eller kiosk-vy beroende
      på session. Kräver HTTPS eller localhost (getUserMedia-policy).
      Stabil UUID istället för URL i QR-koden: inte gjort - befintliga
      utskrivna etiketter kodar `<base>/p/<public_id>`. Public_id är
      redan en UUID-baserad sträng så att byta till QR med bara id
      skulle vara backwards-kompatibelt för in-browser-skannern men
      bryta för externa kamera-appar som öppnar URL:en direkt.
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
- [ ] **Genomgång av roller och behörigheter**: idag finns tre roller
      (reader, editor, admin) med ganska grov uppdelning. Dokumentera
      i CLAUDE.md eller separat permissions-matris vad varje roll ska
      kunna göra på respektive vy/endpoint. Gå igenom alla routes och
      templates för konsekvens - t.ex. ska reader kunna se /tags men
      inte modifiera, ska editor kunna radera pieces eller bara admin,
      etc. Inkludera även mobilflöden (snabbskanning vs granskning).
- [x] **Anteckning per tagg**: Tag.description-fält. Visas under
      tagg-namnet på /tags-listan och som tooltip + extra rad i
      tag-search-autocompleten. Hjälper användarna förstå taggens
      innebörd.
- [x] **Nästlade/hierarkiska taggar med alias**: `parent_id` på Tag
      så taggar kan grupperas i träd, t.ex. "Kyrkliga handlingar >
      Begravning/Dop/Vigsel/Konfirmation". Visar hierarki i /tags-vyn
      och i tag-search-autocompleten (full sökväg som tooltip).
      Alias-stöd via TagAlias-tabell (tag_id, alias_name) så att
      "Minnesgudstjänst" och "Allhelgona" kan vara samma tagg
      sökmässigt utan att duplicera kopplingar. Påverkar filterlogik
      på /pieces.
- [x] **Sök-baserad tagghantering på noter**: HTMX-baserad
      autocomplete-area på pieces/edit. Sökruta filtrerar befintliga
      taggar live, "+ Skapa ny tagg" om inget matchar, aktiva
      pillar med klick=ta-bort. Toggle och create sker omedelbart
      utan att spara hela formuläret.
- [x] **Bulk-utlån / kundvagn för många noter samtidigt**: LoanBatch-
      modell med status cart/picking/active/returned. En cart per
      användare, "+ Korg"-knapp på placeringar, plats-grupperad cart-vy
      med obligatoriskt syfte och låntagare vid checkout. Plockläge
      med ✓ Hämtad / ✗ Hittade ej per rad (ohämtade raderas vid
      slutregistrering), möjlighet att lägga till noter mitt under
      plockning, PDF-plocklista via WeasyPrint. Delvis och total
      återlämning från batch-detaljvy. Navbar-badge "Korg (N)".
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
- [x] **Streckkod/QR-etiketter på enskilda noter + kioskvy**:
      `Piece.public_id` (UUID-hex) genereras vid skapande och backfillas
      vid migration. `/pieces/{id}/qr.png` ger en QR och `/pieces/qr-labels`
      + `/pieces/qr-labels.pdf` genererar utskrivbara etikett-grids
      (filtrerbara via samma `?unit=`/`?tag=`-syntax som /pieces).
      `/kiosk`-vyn har två-stegs flöde: PIN- eller QR-baserad
      inloggning av låntagare (separat från den fasta kiosk-användaren
      som håller webbsessionen), sen skanna-not-flöde med cart i sidopanel
      och snabb checkout som hoppar pickup-fasen. Auto-logout efter
      registrering. Rate-limit på auth (5 fel/15min, 5 min lockout).
- [ ] **NFC/RFID-tagg-inloggning i kiosken**: Komplement till PIN/QR
      där användaren har en fysisk tagg/kort. Kräver hårdvara (USB
      NFC-läsare som ACR122U eller liknande) plus en bryggtjänst som
      skickar UID:t till kiosken som tangentbordsinput. Förenkling:
      lägg ett "nfc:<uid>"-mönster på samma input-fält som hanterar
      både piece- och user-QR, samt en NFC-UID-lista per User (många-
      till-en så samma användare kan ha både kort och armband).
- [x] **Psalmnummer som strukturerad psalmref istället för fritext-fält**:
      ny PsalmBook + PiecePsalmRef-modell. Admin-CRUD på
      /admin/psalmbooks. Flera referenser per not via lägg-till-form
      på piece/edit (bok + utgåva + nummer). Visning som badge-pillar
      på detail/edit. psalm_number-fältet borttaget från forms men
      lämnat i Piece-modellen (för senare datamigrering).
- [x] **Besättning som strukturerade taggar**: voicing är nu TagKind.VOICING
      istället för fritext på Piece. 14 vanliga voicings (SATB, SAB, SSA,
      SSAA, TTBB, ATB, SA, unison, kanon, solo, solo + kör, barnkör m.fl.)
      seedas via tags.yaml. Filtret på /pieces filtrerar tags med
      kind=voicing. /pieces visar voicing-pillar via _voicings_by_piece.
      Sätts via samma tag-modal som andra taggar.
- [x] **Sök och sortera /pieces på inläggningsdatum**:
      sort-dropdown (nyast/äldst/titel A-Ö/Ö-A) + period-dropdown
      (alltid/7/30/90 dagar).
- [x] **Senaste noter på startsidan**: 8 senast inlagda som
      thumbnail-grid ovanför stats-korten.

### V2 - infrastruktur

- [x] **Markdown-stöd för fritextfält**: python-markdown med nl2br,
      sane_lists och tables. Jinja-filter `| markdown | safe` används
      för biografi (person-detalj) och anteckningar (piece-detalj).
      Wikipedia ==-rubriker normaliseras till markdown-rubriker före
      rendering (i `_truncate_wiki_extract`, vid fetch-tid).
      Form-hjälp-texter informerar om format-stöd. EasyMDE-widget
      auto-initialiseras på `textarea.markdown-editor` via base.html.
- [x] **ALTER TABLE-guards i `init_db()`**: schemaändringar tar
      additions-dict i `_ensure_column_guards()` så DB inte behöver
      nukas vid kolumn-tillägg. Reset är destruktivt och kräver
      explicit godkännande från användaren.
- [x] **Psalmböcker som default seed**: `seed_psalms()` anropas
      automatiskt från `seed_all()` med 1986 års svenska psalmbok
      (700 psalmer) + Verbums tillägg 2003 (100 psalmer).
- [x] **Auto-logout i kiosken efter inaktivitet** (commit bd52b0a):
      server-side check i `_kiosk_borrower` som rensar
      `kiosk_borrower_id` om det gått längre än
      `kiosk_idle_timeout_minutes` sedan senaste request. Admin-
      konfigurerbar via `/admin/settings` med default 60 min
      (0 = aldrig logga ut).
- [ ] **Inventering via kiosken**: idag görs `InventorySession` via
      vanliga vyer på /inventory. Naturligare flöde: stå vid kiosk-
      datorn, autentisera med PIN, skanna noter en åt gången för att
      kvittera "den här är på platsen". Skanningar kopplas till en
      aktiv inventeringssession knuten till kioskens lagringsplats.
      UI: list-baserad inventeringsläge-vy där varje not visar
      checked-status, anteckningar och historik. Bör fungera med
      både QR och fritext-sök.
- [ ] **Förlag som strukturerad entitet** (liknande Person-modellen):
      `Publisher`-tabell med name + sort_name + ev. country, IMSLP-länk,
      hemsida, beskrivning. `Piece.publisher` (fritext) ersätts med
      FK till Publisher (eller PieceContributor-stil länktabell om en
      not kan ha flera utgivare). UI: autocomplete-fält som matchar
      befintliga förlag eller skapar nytt. Vid OCR/Claude Vision: matcha
      extraherad förlagstext mot befintliga Publisher-namn med fuzz-score
      innan ny post skapas - hjälper mot stavningsvarianter ("Verbum",
      "Verbum Förlag", "Verbum AB"). Migration kräver dedup av befintliga
      fritext-värden. Tar an `MUSIC PUBLISHER`-MBID-länkningen från
      MusicBrainz om relevant så Wikipedia-länk + logotyp kan följa med.

### UX-konsekvens

- [x] **UX-audit 2026-05-28** (commit 4d0b4db): systematisk genomgång
      av UX/UI-inkonsekvenser dokumenterad i `docs/ux-audit.md`.
      Normaliserade empty-states, knapptext, datumformat, separator-
      tecken i path-byggen, svensk översättning av ScanStatus.
- [x] **HTMX-modaler → Bootstrap-modaler**: storage-modalerna
      konverterade till riktiga Bootstrap-modaler via ny
      `storage/_form_modal.html`-partial. Form-fragment returnerar bara
      `modal-content`-form, modalen öppnas efter `htmx:afterSwap` så
      submit garanterat går mot rätt entitet. Update-routen tar
      `return_to`-parameter så modalen redirectar tillbaka till rätt sida.
- [ ] **Mobil-UI/UX-genomgång**: systematisk granskning av alla vyer
      i smal viewport (typiskt 360-400px). Inget element ska orsaka
      horisontell scroll, bredda containern eller bli större än
      viewporten. Granska särskilt: tabeller (table-responsive eller
      kort-vy?), filterformulär (vertikal stackning på mobil), modaler
      (modal-fullscreen-sm-down?), navbarmenyn (Skanna-knapp + alla
      reguljära items får plats i hamburgermenyn), pill-makron
      (overflow-wrap), embeddade `<pre>`/`<code>`-block, pickup-listor,
      kiosk-vyn. Testa på en riktig telefon, inte bara DevTools.
- [x] **Jinja-macro `place_breadcrumb`**: skapad i `_macros.html` och
      utrullad på `loans/list.html`, `loans/_placement_search.html`,
      `pieces/kiosk.html`, `pieces/kiosk_piece.html`, `pieces/detail.html`
      (modal), `pieces/_kiosk_search_results.html`. Lämnade som-är:
      PDF-templates, tooltips, redan-länkade paths där pill-byte
      skulle bryta klickytan.

## V3 - "Långt fram"

- [ ] **Batch-skanningsläge**: stå framför en hylla och skanna en not
      efter en annan i ren rytm utan navigering mellan submits. Form
      skickas i bakgrunden (HTMX), nollställs, kameran direkt redo för
      nästa not. Persistera placering över submits ("samma plats för
      alla"). Live-feed av redan skannade noter ovanför formuläret.
      Nuvarande snabbskanning fungerar tillräckligt bra och snabbt -
      detta är optimering av rytmen, inte ny funktionalitet.
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
