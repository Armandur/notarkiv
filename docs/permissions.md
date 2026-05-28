# Roller och behörigheter

Det här dokumentet beskriver behörighetsmodellen i notarkiv. Uppdateras
samtidigt som `app/deps.py` eller route-policies ändras.

## Roller

Definierade i `app/models/user.py::Role`:

| Roll | Värde | Beskrivning |
|------|-------|-------------|
| `READER` | `"reader"` | Anställda musiker och körledare som behöver söka och låna noter men inte är fullt betrodda att skanna eller ändra metadata. Får läsa hela katalogen och hantera sin egen kundvagn/sina lån. |
| `EDITOR` | `"editor"` | Betrodda redigerare bland personalen. Kan skanna nya noter, ändra metadata, hantera taggar, skapa lagringsenheter, registrera lån åt andra. |
| `ADMIN` | `"admin"` | Bibliotekarier / systemansvariga. Allt editor kan + radera entiteter, hantera lagringsplatser, psalmböcker, användare, kiosker, inställningar. |

`User.can_edit` är `True` för EDITOR och ADMIN. `User.is_admin` är `True`
bara för ADMIN.

## Dependencies (`app/deps.py`)

| Dependency | Krav | Returnerar | Används av |
|------------|------|------------|------------|
| `current_user` | `user_id` i sessionen | `User \| None` | Helt publika sidor som vill visa "Inloggad som ..." |
| `require_auth` | inloggad | `User` (vilken roll som helst) | GET-listor, detaljvyer |
| `require_editor` | `can_edit` | `User` (EDITOR eller ADMIN) | POST som skapar/ändrar entiteter |
| `require_admin` | `is_admin` | `User` (ADMIN) | Hård radering, locations, taggar, admin-vyerna |
| `require_kiosk_session` | `kiosk_id` i sessionen | `Kiosk` | Kiosk-routes (`/kiosk/*`) - separat från user-auth |
| `require_cart_actor` | inloggad ELLER kiosk-PIN-autentiserad | `User` (med `can_edit`) | Kundvagn-actions så PIN-användare i kiosken kan lägga till/ta bort utan att vara browser-inloggade |

`verify_csrf` används på alla POST-routes. Inte en auth-dependency men
hanteras i samma lager.

## Behörighetsmatris per resurs

R = Reader, E = Editor, A = Admin, K = Kiosk (PIN-autentiserad i kiosk)

### Pieces (noter)

| Åtgärd | R | E | A | Anmärkning |
|--------|---|---|---|------------|
| Lista (`GET /pieces`) | ✓ | ✓ | ✓ | |
| Detalj (`GET /pieces/{id}`) | ✓ | ✓ | ✓ | |
| Skriv ut/PDF | ✓ | ✓ | ✓ | |
| QR-etiketter (PDF/HTML) | ✓ | ✓ | ✓ | |
| Skapa manuellt (`POST /pieces`) | | ✓ | ✓ | |
| Redigera metadata | | ✓ | ✓ | |
| Lägg till bilder | | ✓ | ✓ | |
| Lägg till placering | | ✓ | ✓ | |
| Re-OCR | | ✓ | ✓ | |
| Hantera taggar (toggle, skapa via piece) | | ✓ | ✓ | Enskilda taggar - men ej hierarki |
| **Radera** | | | ✓ | Hård radering inkl. bilder och placeringar |

### People (personer)

| Åtgärd | R | E | A | Anmärkning |
|--------|---|---|---|------------|
| Lista, detalj | ✓ | ✓ | ✓ | |
| Skapa (sker auto vid piece-spara) | | ✓ | ✓ | Via `replace_contributors` |
| Redigera | | ✓ | ✓ | |
| Koppla MB/Wikidata | | ✓ | ✓ | |
| Lägg till alias/länkar | | ✓ | ✓ | |
| Radera person | | | ✓ | Tillåts bara om personen inte kopplad till noter |
| Lista och radera orphaned | | | ✓ | `/people/orphaned` |

### Storage (lagringsplatser och enheter)

| Åtgärd | R | E | A | Anmärkning |
|--------|---|---|---|------------|
| Lista/träd | ✓ | ✓ | ✓ | |
| Detalj per enhet | ✓ | ✓ | ✓ | |
| QR-etiketter | ✓ | ✓ | ✓ | |
| **Skapa/ändra/radera lagringsplats** | | | ✓ | Locations är admin-territorium |
| Skapa/ändra/radera enhet (unit) | | ✓ | ✓ | Daglig arbetsuppgift |
| Hantera enhetstyper (UnitKind) | | | ✓ | Via `/admin/unit-kinds` |

### Loans (utlån) + Cart (kundvagn)

| Åtgärd | R | E | A | K | Anmärkning |
|--------|---|---|---|---|------------|
| Lista, batch-detalj | ✓ | ✓ | ✓ | | |
| Mina lån-sidopanel | ✓ | ✓ | ✓ | | |
| Se egen kundvagn | ✓ | ✓ | ✓ | ✓ | Reader får också låna |
| Lägg i kundvagn | ✓ | ✓ | ✓ | ✓ | |
| Bulk-lägg från lagringsenhet | ✓ | ✓ | ✓ | ✓ | |
| Ändra antal / ta bort ur korg | ✓ | ✓ | ✓ | ✓ | |
| Skapa batch (checkout) | ✓ | ✓ | ✓ | ✓ | Reader får bara välja sig själv eller extern fritext-låntagare - inte andra interna |
| Skapa direkt enskilt lån (piece-modal) | | ✓ | ✓ | | Admin-aktigt: registrera lån åt någon annan |
| Plocklista-flöde | | ✓ | ✓ | | Plockning är arbetsuppgift |
| Aktivera batch (slut-checkout) | | ✓ | ✓ | | |
| Återlämna lån | ✓ | ✓ | ✓ | ✓ | Via `require_cart_actor` så reader kan återlämna |
| **Radera lån** | | | ✓ | | Permanent borttagning av rad |

### Scan (skanning)

| Åtgärd | R | E | A | Anmärkning |
|--------|---|---|---|------------|
| Ladda upp ny skanning | | ✓ | ✓ | |
| Snabbskanning (mobil) | | ✓ | ✓ | |
| Granska skanning | | ✓ | ✓ | |
| Spara skanning som piece | | ✓ | ✓ | |
| Lägg till i befintlig piece | | ✓ | ✓ | |
| Avvisa (mjuk-radera) | | ✓ | ✓ | `discarded`-flag |
| Återställ avvisad | | ✓ | ✓ | |
| **Radera permanent** | | | ✓ | DB + bildfiler från disk |
| Re-OCR via piece-edit | | ✓ | ✓ | Skapar ny scan i kön |

### Inventory (inventering)

| Åtgärd | R | E | A | Anmärkning |
|--------|---|---|---|------------|
| Lista, detalj | | ✓ | ✓ | Reader saknas - tänk på? |
| Skapa session | | ✓ | ✓ | Multi-session per användare stöds |
| Avsluta session | | ✓ | ✓ | |
| Checka enheter | | ✓ | ✓ | |

### Tags (taggar)

| Åtgärd | R | E | A | Anmärkning |
|--------|---|---|---|------------|
| Lista hierarki | ✓ | ✓ | ✓ | |
| Toggle tagg på piece | | ✓ | ✓ | Via piece-flow |
| Skapa ny tagg via piece | | ✓ | ✓ | Snabb skapelse i tagg-area |
| Skapa/ändra/radera tagg via `/tags` | | ✓ | ✓ | Hierarkihantering |
| Hantera alias | | ✓ | ✓ | |

### Admin-vyer (`/admin/*`)

Alla `/admin/*`-routes kräver ADMIN. Inkluderar:
- `/admin/users` - användarhantering, roll-byten, lösenordsåterställning
- `/admin/kiosks` - kiosk-registrering och tokens
- `/admin/settings` - API-nycklar, modellval, kiosk-timeout
- `/admin/jobs` - jobb-status, requeue
- `/admin/psalmbooks` - psalmboksregister
- `/admin/unit-kinds` - autocomplete-databas för enhetstyper

### Kiosk (`/kiosk/*`)

Kiosk-routes är *separata* från user-auth. Webbsessionen är aktiverad
som kiosk via `kiosk_id` i `request.session`. Aktivering sker via
`POST /kiosk/activate` (kräver kiosk-token från admin) eller via
admin-länk efter användarinloggning.

Inom en kiosk-session kan en låntagare PIN-autentisera sig
(`kiosk_borrower_id` i sessionen). PIN-autentiserade låntagare räknas
som "editor"-actor i `require_cart_actor` så de kan hantera utlån utan
att vara browser-inloggade.

## Profil-routes

Alla under `require_auth`:
- `GET/POST /profile` - se egen profil
- `POST /profile/pin` - sätt/byt PIN
- `POST /profile/pin/clear` - rensa PIN
- `POST /profile/kiosk-token/regenerate` - ny QR-token
- `GET /profile/kiosk-qr.png` - ladda ner egen QR
- `POST /change-password` - byt lösenord

## Designval och avgränsningar

1. **Inventory är editor-bara** (medvetet). Inventering är en
   städningsoperation för bibliotekarier - att se "saknas"-noter eller
   logghistorik är inte relevant eller kan rentav vara förvirrande för
   vanliga körmedlemmar. Reader ser därför ingenting av inventory-
   flödet.

2. **Cart/utlåning är öppet för alla auth:ade** (inkl reader). Det är
   körlagets dagliga arbete att låna noter, så `require_cart_actor`
   kräver bara giltig session - inte editor-roll. Detta gäller även
   PIN-autentiserade kiosk-låntagare.

3. **Skapa direkt enskilt lån från piece-modal** kräver editor.
   Skillnaden mot cart-flödet: enskilt lån = "registrera lån åt någon
   annan med specifika fält" (bibliotekarie-uppgift). Cart-flödet =
   "jag lånar dessa noter åt mig själv" (vanligt körmedlem).

4. **Tagghantering**: editor kan nu redigera, lägga till alias och
   skapa/ändra/radera taggar via `/tags`. Bara hård radering av piece
   och location är fortsatt admin-bara.

5. **Indexsidan** (`GET /`) använder `current_user` (mjuk-auth) men
   redirectar oinloggade till `/login` manuellt. Fungerar, men hade
   varit renare att använda `require_auth` direkt.
