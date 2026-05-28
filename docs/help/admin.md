# Guide för admin

Du har **admin**-rollen och kan utöver alla editor-funktioner också
hantera systemet, användare och toppnivå-strukturer.

## Användare

**Admin → Användare**:

- Skapa nya användare med roll (reader/editor/admin)
- Auto-genererat lösenord som måste bytas vid första login
- Återställa lösenord för någon som glömt
- Ändra roll via dropdown direkt i tabellen
- Radera användare permanent (egen rad disablad)

På sidan finns rollöversikt-kort som förklarar vad varje roll får.

## Kiosker

**Admin → Kiosker** är för fysiska kiosk-datorer i förrådet:

- Skapa en kiosk med namn + lagringsplats den är knuten till
- En **access-token** genereras - använd den för att aktivera
  webbläsaren som kiosk via `/kiosk/activate?token=...`
- Token kan regenereras om den läckt
- Kiosken visar `/kiosk` som auth-vy för låntagare (PIN/QR)

Kiosk-låntagare måste vara editor eller admin för att kunna registrera
lån - reader-låntagare kan låna åt sig själva via vanlig browser men
inte använda kioskens vyer.

## Lagringsplatser (toppnivå)

På `/storage` ser du trädet. Du är ensam om att kunna:

- **Skapa nya lagringsplatser** (`StorageLocation`) - "Sakristian",
  "SharePoint", "Notarkivet" etc. Fysisk eller digital.
- **Redigera lagringsplatsens** namn, typ, beskrivning
- **Radera lagringsplats** (alla enheter och placeringar tas också bort)

Editor kan skapa/redigera/radera *enheter* (hyllor, pärmar) inom
befintliga platser - det är dagligt arbete.

## Psalmböcker

**Admin → Psalmböcker** är registret över psalmböcker och sångböcker
som noter refererar till. Default seedas:

- Den svenska psalmboken 1986 (700 psalmer)
- Verbums psalmbokstillägg 2003 (100 psalmer)

Du kan lägga till fler (t.ex. olika sångböcker) och redigera namn,
utgåva, beskrivning och sorteringsordning.

## Enhetstyper

**Admin → Enhetstyper** är autocomplete-databasen för "Typ" på
lagringsenheter ("hylla", "pärm", "låda", "mapp"). Skapa, redigera,
radera. Dubletter blockeras automatiskt.

## Inställningar

**Admin → Inställningar** styr runtime-konfig utan omstart:

- **Anthropic API-nyckel** för Claude Vision (lagras klartext i SQLite)
- **Claude-modell** - default `claude-haiku-4-5` (billigast, räcker för
  notomslag)
- **OCR-default** - vilken provider som är förvald i skanningsflödet
- **MusicBrainz User-Agent** - krav från MB:s villkor, ange kontakt-info
- **Kiosk-timeout** - hur länge en PIN-autentiserad låntagare är
  inloggad utan aktivitet (default 60 min, 0 = aldrig)

## Jobb-administration

**Admin → Jobb** visar:

- **Fastnade skanningar** - jobb som inte slutfört på rimlig tid.
  Klicka **"Återstartka alla"** för att kö:a om dem.
- **Senaste misslyckade** - jobb som kraschade med felmeddelande.
  Återstart-knapp finns.

Använd när skanningar verkar "hänga" - vanligtvis betyder det att
worker-processen fastnade (kolla `worker.log`) eller att Anthropic
API:t hade hicka.

## Hård radering

Du är ensam om att kunna **hård-radera** entiteter permanent:

- **Noter** - tar bort piecen, alla bilder, placeringar, lån-historik,
  tagg-kopplingar
- **Personer** - bara om personen inte är kopplad till noter
- **Skanningar** - inklusive bildfiler från disk
- **Lån** - tar bort raden ur historiken

Före radering visas en bekräftelsedialog. Tänk på att hård radering
**inte kan ångras**. Om i tveksamhet, använd avvisa (skanningar) eller
markera arkiverad (storage) först.

## Backup och säkerhet

Backup-skripten ligger i `scripts/`:

- `backup.sh` - SQLite-snapshot + rclone-upload till offsite
- `restore.sh` - hämtar senaste eller specifik snapshot

Verifiera regelbundet att restore fungerar. Lokala snapshots ligger
i `snapshots/` (gitignored).

API-nyckel för Anthropic och MusicBrainz User-Agent ändras via
inställningar utan kod-deploy. SESSION_SECRET sätts i `.env`.

## När någon glömt sitt lösenord

På `/admin/users` klicka **Återställ lösen** på raden. Ett nytt
tillfälligt lösenord visas i flash-meddelandet - skicka det till
användaren via säker kanal (Signal etc.). Användaren tvingas byta
vid nästa login.

## När någon glömt sin PIN

Användaren kan själv rensa och sätta ny PIN på `/profile`. Admin har
inte direkt åtkomst att sätta annans PIN - för säkerhet hashas alla
PINar.
