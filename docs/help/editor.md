# Guide för editor

Du har **editor**-rollen och kan utöver alla reader-funktioner också
skanna, redigera och hantera lagring. Läs gärna reader-guiden först.

## Skanna nya noter

Du har två skanningssidor:

- **Skanna ny not** (`/scan`) - desktop-anpassat, en bild i taget,
  bra för stationärt arbete
- **Snabbskanning** (`/scan/quick`) - mobil-anpassad, flera bilder
  per not, bra för att gå i förrådet

### Flödet

1. Ta bild av notomslaget med kameran
2. Auto-crop körs direkt (jscanify hittar kanter, du kan justera
   manuellt via ⊡-knappen om det blir fel)
3. Ladda upp - skanningen läggs i granskningskön
4. OCR (Claude Vision som default) extraherar titel, kompositör,
   tempo, besättning, språk osv.
5. Du granskar och rättar i **Granskningskö** (`/scan/queue`)
6. Klicka **Spara** för att skapa noten

### Dubblettkoll

Vid granskning visas en varning om en liknande not redan finns. Då
kan du välja att **lägga till en placering** på den befintliga noten
istället för att skapa en dublett.

### Avvisa skanningar

Klicka **Avvisa** för att gömma en skanning som är suddig, dublett
eller felaktig. Avvisade ligger kvar i kön men gömda (toggla med
"Visa avvisade"). Admin kan hård-radera dem permanent.

## Redigera metadata

På piece-detalj klickar du **Redigera**:

- All metadata (titel, kompositör, arrangör, textförfattare, etc.)
- Person-fält söker mot **MusicBrainz/Wikidata** - klicka pillarna
  för att applicera förslag
- Lägg till **bilder** (omslag, baksida, försättsblad, insida)
- Lägg till/ändra **placeringar** (var noten ligger)
- Lägg till **taggar** för besättning, språk, liturgisk kategori
- Lägg till **psalmreferens** om noten finns i en psalmbok
- **Personliga anteckningar** (egna tonarter, repetitionsnoter etc.) -
  syns bara för dig

### Re-OCR

Om OCR missade något kan du klicka **Kör OCR igen** - en ny skanning
läggs i kön baserad på primärbilden. Du granskar som vanligt.

## Hantera taggar

Gå till **Taggar** i menyn för att se hierarkin (besättning, språk,
tillfälle, fria taggar). Du kan:

- Skapa, redigera, radera taggar
- Lägga till **alias** ("Minnesgudstjänst" → "Allhelgona") så sökning
  fungerar oavsett stavning
- Sätta en kort beskrivning som visas som tooltip
- Bygga **hierarkier** (t.ex. "Kyrkliga handlingar" > "Begravning")

## Hantera lagring

Gå till **Lagringsplatser**. Du kan inte skapa nya toppnivå-platser
(det är admin), men du kan:

- **Skapa nya enheter** (hyllor, pärmar, mappar) inom befintliga platser
- **Redigera enheter** - namn, typ, anteckningar
- **Ta bort enheter** (varning om noter finns där)
- **Skriva ut QR-etiketter** för enheter

På varje enhets detaljsida ser du noterna som ligger där och kan
bulk-lägga dem i utlåningskorgen.

## Inventering

**Inventering** i menyn låter dig starta en **session**:

1. Starta ny session, ange ett namn (t.ex. "Sopran-pärm 2026")
2. Välj planerad placering (valfritt)
3. Skanningar du gör knyts auto till sessionen
4. Gå till **Inventeringsläge** för att checka av enheter en åt en:
   - ✓ Hittad
   - ⚠ Avvikande antal
   - ✗ Saknas
5. Sessionen har en aktivitetslogg där både systemet och du kan
   skriva noteringar
6. Avsluta sessionen när du är klar

Du kan ha flera sessioner igång parallellt - andra användare
påverkas inte.

## Registrera lån åt andra

Om du behöver registrera ett enskilt lån åt någon annan (t.ex.
körledare som hämtar åt sin sektion), klicka **"+ Lån"** på en
placering. Det skapar ett enskilt lån med specifika fält direkt -
utan att gå via kundvagn.

## Mobil-flöden

På telefon fungerar:

- **Snabbskanning** med auto-crop och kamera-input
- **QR-skanner i navbaren** (`📷 Sök QR`) för att öppna noter
- **Mina lån-sidopanel** i kiosken via PIN-inloggning
