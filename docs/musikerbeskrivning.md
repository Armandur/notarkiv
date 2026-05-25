# Notarkiv - vad systemet gör och vad det vet

Detta är en sammanfattning av vad systemet kan idag (våren 2026), tänkt
att läsa för en musiker som ska tycka till om funktionalitet och språk.
Den som byggt det är inte musiker själv - så hör av dig om något låter
fel eller om viktiga grejer saknas.

## Tre grupper som använder systemet

- **Läsare** - alla i arbetslaget. Kan logga in, söka, se var noter finns,
  läsa egna och andras anteckningar, se utlåningar.
- **Granskare/redigerare** - de som faktiskt registrerar noter. Kan
  skanna, redigera, lägga till bilder, hantera personer och placeringar,
  registrera utlån.
- **Administratör** - skapar konton, sätter API-nycklar, åtgärdar
  fastnade skanningsjobb.

## Vad systemet känner till

### Noter (Pieces)

En "not" i systemet är en specifik utgåva av ett verk - alltså om ni har
både Bärenreiter-utgåvan och Carus-utgåvan av samma Mozart-mässa så är
det två separata poster.

För varje not lagras:

- **Titel** och eventuell **originaltitel** (för översatta verk - t.ex.
  svensk titel + tysk originaltitel)
- **Kompositör, arrangör, textförfattare** - som länkar till
  **person**-poster, inte fria texter. Det betyder att "Felix
  Mendelssohn" är samma sak överallt och att man kan klicka på namnet
  för att se alla noter den personen är inblandad i.
- **Besättning** (SATB, SAB, SSA, unison, solo m.fl.)
- **Ackompanjemang** (a cappella, piano, orgel, annat)
- **Språk** på texten (ISO-kod som "sv", "en", "la", "de")
- **Förlag** och **förlagsnummer/edition**
- **Psalmnummer** (om kopplat till psalmboken - för svenska kyrkans verk)
- **Anteckningar** (gemensam fritext)
- **MusicBrainz work-ID** (om matchat mot deras databas)
- **Bilder** - en eller flera (framsida, baksida, försättsblad)
- **Taggar** - för kategorisering (se nedan)
- **Placeringar** - var i förrådet noten finns (se nedan)

Förslag att tycka till om:
- Är det rätt nivå av detalj? Saknas något?
- Behövs **opus-nummer** och **katalogsnummer** (BWV, KV osv) som egna
  fält, eller är "förlagsnummer/edition" tillräckligt?
- Behövs **svårighetsgrad** och **ungefärlig längd**? (Finns som fält,
  bara inte exponerat i formulär än.)
- Behövs **rättigheter/copyright-status** mer prominent (original,
  licensierad kopia, public domain)?
- **Stämma**-information (sopran, alt, tenor, bas i klaver) - är det
  något vi ska kunna ange per piece, eller är det självklart av
  besättningen?

### Personer

Kompositörer, arrangörer och textförfattare lagras som egna
**person**-poster, inte som löst text. För varje person:

- **Namn** (visning) och **sorteringsnamn** ("Mendelssohn, Felix")
- **Födelse- och dödsdatum** (kan vara bara år, år+månad eller
  fullständigt - "1809" eller "1809-02-03" funkar)
- **Land** (ISO-kod, visas som flagga + svenskt namn: 🇩🇪 Tyskland)
- **Biografi** (fritext) - hämtas automatiskt från Wikipedia om
  MusicBrainz-match görs
- **Porträtt** - laddas upp manuellt eller hämtas automatiskt från
  Wikimedia Commons via MusicBrainz
- **Länkar** - generiska länkar med typ (Wikipedia, MusicBrainz, IMSLP,
  YouTube, Spotify, officiell, annat)
- **MusicBrainz artist-ID** för automatiska uppdateringar

Förslag att tycka till om:
- Räcker det med kompositör/arrangör/textförfattare, eller ska vi också
  ha **dirigent**, **redaktör**, **översättare** (finns som val,
  exponerade vid behov)?
- Vill man se **levande personer** mer aktivt (kanske notera "kontakta
  för tillstånd vid framförande")?

### Lagringsplatser

Systemet känner till var noterna fysiskt eller digitalt finns. Strukturen
är nästlad:

- **Lagringsplats** (rum/system, t.ex. "Notarkivet", "Sakristian",
  "SharePoint", "Teams")
- **Förvaringsenheter** (under en lagringsplats, kan nästlas godtyckligt
  djupt: "Hylla A" → "Pärm 3" → "Mapp Q")
- **Typ av enhet** (hylla, pärm, låda, mapp - kan utökas vid behov)

Digitala lagringsplatser fungerar likadant men taggas som digitala.
Exempel: "Teams › Musikerkanalen › Notermappen".

### Placeringar

En **placering** kopplar en specifik not till en specifik enhet med
**antal exemplar**. Samma not kan ligga på flera platser (t.ex. 25 ex i
huvudarkivet och 3 arbetskopior i sakristian) - varje plats är en egen
placering.

För digitala lagringar lämnas antal-fältet tomt (det är ju bara en
plats där filen finns).

Förslag att tycka till om:
- Vi har inte stöd för **stämuppdelning per placering** ("5 sopran-
  stämmor + 5 alt-stämmor"). Behövs det, eller är "25 ex" tillräckligt?
- **Skick** per exemplar/placering ("välbevarad", "behöver bindas om")
  - relevant?

### Utlåningar

Per placering kan man registrera att N exemplar är utlånade till en
person med:

- Låntagarens namn (idag fritext - planerat att bli koppling till
  systemets användare där det går)
- Antal exemplar
- Datum när det lånades
- Eventuellt förväntat återlämningsdatum
- Anteckning

Status visas som "X/Y ex hemma" + "Z utlånade" per placering. Återlämning
markeras med ett klick.

Förslag att tycka till om:
- Räcker det med själva utlåningsregistrering, eller behövs ett
  **e-postutskick** vid passerat returdatum?
- Vill man kunna **boka/reservera** noter inför framtida bruk
  (annorlunda från utlån)?

### Taggar

Noter kategoriseras med taggar i tre typer:

- **Kyrkoåret** (advent, jul, fasta, påsk, pingst, allmän gudstjänst
  m.fl.) - seedas in från start
- **Tillfälle** (begravning, bröllop, dop, konfirmation, skolavslutning
  m.fl.)
- **Fria taggar** - vad man vill (barnkör, luciatåg etc.)

Användarna kan skapa fria taggar löpande.

Förslag att tycka till om:
- Saknas några kategorier i kyrkoåret eller tillfällen?
- Behövs **musikgenre/period** som tagg-typ (barock, renässans, folkmusik,
  pop)?
- Behövs **stil/karaktär** (lugn, festlig, sorg)?

### Personliga anteckningar

Förutom de gemensamma "anteckningar"-fältet på noten kan varje inloggad
användare ha en egen privat anteckning per not (för tonart i ens stämma,
tempo, repetitionsnoter m.m.). Andra användares anteckningar visas men
med tydligt avsändarnamn.

Förslag att tycka till om:
- Räcker fritext, eller vill man kunna **strukturera** sina egna
  noteringar (t.ex. "egen tonart: G-dur" som eget fält)?

## Hur det funkar i praktiken

### Lägga in en not (de tre vägarna)

**A. Skanning från bild** (vanligast - körledaren på plats med mobilen):

1. Person 1 (vid hyllan, telefon): `/scan/quick` - kameran öppnas,
   tar bild på framsidan, kan rotera och lägga till bak- eller
   försättsblad innan upload, väljer eventuellt placering, klickar
   "Skanna och fortsätt"
2. Systemet OCR:ar bilden med Claude Vision (eller Tesseract) i
   bakgrunden
3. Systemet söker MusicBrainz efter matchningar
4. Skanningen hamnar i en **granskningskö**
5. Person 2 (vid datorn): tar nästa post från kön, granskar de
   automatiskt ifyllda fälten, godkänner eller rättar, eventuellt
   applicerar MusicBrainz-förslag
6. Spara → ny piece skapas, eventuell ny person automatiskt också

**B. Vid samma plats, scanning + review direkt** (`/scan`): hela
flödet på en gång, för enskild dator-användare.

**C. Manuellt** (`/pieces/new`): om noten inte ska skannas - t.ex. om
man har metadata utan att ha noten fysiskt eller om bilden ändå inte
ger något.

### Inventeringstillfälle

Innan en "inventeringsdag" startar man ett **inventeringstillfälle**
med namn ("Hylla A - 2026-05-25") och valfri planerad plats. Alla
skanningar gjorda när det tillfället är aktivt knyts dit. Man kan
också gå till **inventeringsläget** och systematiskt checka av
placeringar i en enhet ("vad ska ligga i Pärm A3? Är det det?").

### Sök och bläddra

På `/pieces` finns flera filter: fritext, taggar, besättning, språk,
plats (inkl underenheter), samt val mellan kort-/list-/trädvyer.

Trädvyn visar hierarkin lagringsplatser → enheter med antal placeringar
per nivå, så man kan börja från "Hylla A" och se vad som finns.

### Personer

Egen översikt `/people` med sök och filter på roll, land och
MBID-status. Klick på en person visar alla deras noter grupperade per
roll, biografi, externa länkar.

### Taggar och utlån

Egna översiktssidor: `/tags` (alla taggar med antal noter per tagg),
`/loans` (alla utlån med toggle för att se historik).

### MusicBrainz och Wikipedia

MusicBrainz är en öppen databas över musikverk och kompositörer (ungefär
"IMDb för musik"). Vi använder det för att:

- Få **kanoniska namn** och stavningar (Felix Mendelssohn, inte F.
  Mendelsohn eller liknande)
- Identifiera **dubletter** mellan olika utgåvor
- Hämta **levnadsår**, **land**, **biografi** (via Wikipedia),
  **porträtt** (via Wikimedia Commons), **externa länkar**

Allt detta sker bara om en användare aktivt klickar "Använd" på ett
matchningsförslag - inget skickas till MusicBrainz utan att en
skanning eller manuell sökning gjorts.

Biografier från Wikipedia visas med korrekt CC BY-SA-attribution och
länk till källartikeln.

## Tekniska val som påverkar verksamheten

- **Mobilkamera räcker**: ingen separat skanner behövs. Bilder rakt från
  mobilen funkar bra för OCR.
- **Pris**: Claude Vision (rekommenderad OCR-metod) kostar runt 1-2 öre
  per skanning. För en katalog på 1000 noter pratar vi om en tia totalt.
  Tesseract är gratis men sämre på stiliserade omslag.
- **Backup**: nattlig kopiering till Google Drive (sätts upp via
  rclone). Allt går att återställa.
- **Två-personers-flöde**: skannarbete fördelas naturligt - en med
  mobil, en med dator.

## Det som inte finns än men är planerat

(Saker som har stått på listan och är beslutade som "ja det ska vi
göra", men inte byggts än.)

- Auto-crop av bilder (likt OneDrive-skannern) med dokumentfilter
- In-browser QR-läsare för att scanna platsetiketter direkt i appen
- Borrower som koppling till användare istället för bara fritext
- E-postnotifikationer (för t.ex. försenade återlämningar)
- IMSLP-länkning för fria notutgåvor
- PDF-/MusicXML-uppladdning till själva noten
- Postgres-migration när vi växer ur SQLite

Hela listan finns i `ROADMAP.md`.

## Frågor till en musiker

1. **Vad saknas i datamodellen?** Vilka fält på not, person, plats
   eller tagg borde vi också ha?
2. **Är skannings-OCR-flödet tydligt?** Förstår man som körledare hur
   man tar en bra bild för att få bra resultat?
3. **Räcker den nuvarande nivån av MusicBrainz-berikning**, eller vill
   man ha mer/mindre auto-data?
4. **Hur prioriterar du dem planerade men obyggda funktionerna?**
   Vilken skulle göra störst skillnad?
5. **Finns det rutiner i en församling/körverksamhet vi missat helt?**
   T.ex. årsavstämning, framförandeprotokoll, hyrnoter-spårning?
