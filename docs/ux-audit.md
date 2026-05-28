# UX/UI-audit 2026-05-28

Systemgenomgång efter en längre utvecklingsperiod. Punkterna är
faktiska inkonsekvenser i nuvarande kod, inte personliga åsikter. Vi
arbetar igenom dem en kategori i taget och du säger "fixa", "skippa"
eller "diskutera vidare" per punkt.

Numreringen är stabil - referera till "U1", "K3" osv när vi går igenom.

---

## U. Tomma tillstånd ("Inga X...")

Idag finns minst 5 olika formuleringsmönster sida vid sida:

| Fras | Förekommer i |
|------|--------------|
| `Inga X än` | personer, alias, enheter med placeringar, kiosker, lagringsplatser |
| `Inga X ännu` | enheter, lagringsplatser, QR-token |
| `Inga X registrerade än` | personer, kiosker, lagringsplatser |
| `Inga X.` (punkt) | scan-kö, taggar |
| `Inga X med filtren.` | personer (filtrerat läge) |

**U1.** Bestäm en mall: rekommendation
`Inga X ännu.` (utan "registrerade", utan tankstreck), och
`Inga träffar med valda filter.` för filtrerade listor.

**U2.** Vissa empty-states har handlingsförslag inbakade ("Skapa den
första via knappen ovan", "skapas när du sparar noter med..."), andra
inte. Lägg till handlingsförslag konsekvent där det finns en uppenbar
nästa åtgärd.

---

## D. Datum/tid-format

I templates förekommer tre format parallellt:

- `strftime('%Y-%m-%d %H:%M')` - 25 ställen (default i loggar och listor)
- `strftime('%Y-%m-%d')` - 11 ställen (typiskt återlämnings-datum, due-datum)
- `strftime('%H:%M')` - 2 ställen (samma-dag-context i inventeringslogg)

**D1.** OK att ha både datum-only och datum+tid - men gör det
*förutsägbart*: "när hände det" = datum+tid, "när ska/skedde detta
fält" = bara datum.

**D2.** `strftime('%H:%M')` utan datum är förvirrande om man scrollar
en logg över flera dagar. Använd alltid minst datum.

---

## K. Knapptext-terminologi

Verbval ser delvis slumpmässigt ut:

- **Spara** (10x) - används brett, OK
- **Skapa** (8x) - används vid nya entiteter (Skapa låntagare, Skapa
  utlåning, Skapa plats)
- **Lägg till** (5x) - används vid att utöka en lista (Lägg till
  placering, Lägg till alias)
- **Använd MB / Använd WD / Använd förslag** - blandar konsekvens
- **Registrera** - används både för "Registrera lån" och "Registrera
  återlämning" (verbet är OK men blandas in i Spara/Skapa-listor)

**K1.** Bestäm regeln: **Skapa** = ny rad i en tabell, **Lägg till** =
extra rad i en redan-existerande relation, **Spara** = ändring av
befintligt. Idag är t.ex. "Lägg till plats" för en ny plats fel
enligt den regeln (borde vara Skapa).

**K2.** "Använd MB" vs "Använd WD" bryter normalt UI-mönster.
Föreslå: `Använd MusicBrainz` / `Använd Wikidata` (eller bara `Använd`
med en titel-tooltip om utrymme saknas).

**K3.** Toppknappar för "lägg till ny X" har olika prefix:
`+ Nytt utlån`, `+ Lägg till`, `+ Lägg alla i utlåningskorg`. Gör om
till verb-baserat utan plus-tecken: `Nytt utlån`, `Ny plats`, `Ny
person`.

---

## P. Platsrendering (var ligger noten)

Detta är ett återkommande mönster med stora inkonsekvenser:

- **Vissa templates**: `{{ placement.unit.path }}` (rendrad
  breadcrumb-sträng: "Sakristian / Skåp A / Hylla 2")
- **Vissa**: `{{ placement.unit.name }}` (bara sista delen)
- **Vissa**: manuellt sammanfogad `{{ location.name }} / {{ unit.name }}`
- **Pickup-sidan**: bara `unit.name` utan kontext (du nämnde detta
  tidigare)

**P1.** Skapa en Jinja-makro `unit_breadcrumb(unit)` som rendrar
hierarkin som pills (samma stil som /kiosk-vyn). Använd överallt.

**P2.** I tabeller där utrymme är knappt: visa fullständig sökväg som
tooltip, sista delen som pill.

---

## H. Rubriknivåer (h1/h2/h3)

Bootstrap-konventionen `h1 class="h3 mb-3"` används inkonsekvent:

- Vissa sidor börjar `<h1 class="h3">` (semantiskt korrekt, visuellt
  mindre)
- Vissa börjar med `<h2>` utan h1 alls
- Card-headers använder ibland `<h2 class="h6">`, ibland en oformaterad
  div, ibland `<h5>`

**H1.** Alla sidor ska ha exakt ett `<h1>` (kan vara `class="h3 mb-3"`
visuellt). Card-headers ska vara `<h2 class="h6 mb-0">` konsekvent.

---

## F. Filter-formulär i listor

Listsidor har var sin variant av filterform:

- /pieces - GET-form med Filtrera + Rensa
- /people - dito men annan knappstorlek
- /loans - dito, andra färger på primary
- /scan-queue - inga filter
- /tags - inga filter

**F1.** Standardisera filter-form-layout via en partial
`_list_filter.html` med konsekvent knappstil
(`btn btn-outline-secondary` för Rensa, `btn btn-primary` för
Filtrera, båda `btn-sm`).

**F2.** Vissa filter persistar genom navigering (query-string),
andra inte. Bestäm policy. Default = persistera, behöver "Rensa"-knapp
synas alltid när filter är aktiva.

---

## B. Badge-färger - vad betyder vad

Färgsemantik är inte konsekvent:

- `bg-success` = aktiv, OK, satt, godkänd
- `bg-warning` = pågående, varning, "saknas i arkivet"
- `bg-danger` = återlämnad-sent, kassera, fel
- `bg-info` = ny, infosignal, "förslag"
- `bg-secondary` = neutral, gammal, inaktiv
- `bg-primary` = ?? används både för räknare och status

**B1.** Bara `bg-secondary` för räknare i navbaren, aldrig
`bg-primary` (eftersom den signalerar "viktig").

**B2.** Inventerings-status använder ✓⚠✗ + färg, samma färgkod ska
finnas i tex piece-detalj ("senast sedd grön/gul/röd") - idag
inkonsekvent eller saknas.

---

## L. Listvy-mönster (kort vs tabell vs list-group)

- /pieces - kort eller tabell (toggle ?view=)
- /scan-queue - kort eller tabell (toggle)
- /people - tabell
- /tags - tabell
- /loans - tabell (med list-group inuti)
- /inventory - list-group
- /storage - trädvy

**L1.** Kort/tabell-toggle är värdefull för pieces och scan-queue.
Övriga listor är OK som tabeller. **Inventory** skulle dock må bra av
en tabell (datum, status, antal-checkar) - idag är list-group rörig.

**L2.** Den blandning av list-group och table i `/loans` är förvirrande.
Bestäm en presentationsform.

---

## M. Modaler vs inline-formulär

Idag används båda parallellt utan tydlig regel:

- **Modaler för**: skapa-tagg, lägg-till-placering, MB-sökning,
  bekräftelsedialoger, MusicBrainz/Wikidata-förslag
- **Inline-form för**: lagringsplatser (manage.html), inventory
  check-läge, piece edit

**M1.** Regel: modaler för åtgärder *inuti en kontext*, inline-form
för dedikerade sidor. Idag är "ny lagringsplats" inline-form i en
modal-style markup, vilket är förvirrande.

---

## C. Confirm-dialoger

Tidigare regel: alltid `confirmAction(url, msg, opts)`, aldrig
native `confirm()`.

**C1.** Verifiera att inga `onclick="confirm(...)"` eller
`<form onsubmit="return confirm(...)">` finns kvar i templates. Grep
har bekräftat att profile.html är fixad - men hela kodbasen bör
sökas igenom.

**C2.** Bestäm en submitClass-konvention: `btn-danger` för
radera/kassera, `btn-warning` för "den gamla slutar funka",
`btn-primary` för bekräfta-OK.

---

## N. Navbar-terminologi

Idag: Översikt, Noter, Personer, Taggar, Kiosk, Skanna,
Lagringsplatser, Admin (när admin)

**N1.** "Skanna" är ett verb, övriga är substantiv. Gör om till
"Skanning" eller flytta in under "Noter > Skanna".

**N2.** "Lagringsplatser" är långt och fyller utrymme. Föreslå
"Platser".

**N3.** Inventering saknas i navbaren - finns bara som länk från
/scan/quick. Borde det vara en topp-nivå-flik?

---

## S. Spacing och topp-knapprader

`d-flex gap-1` (3), `gap-2` (9), `gap-3` (1) blandas. Detta är
visuellt slumpvist olika luft mellan knappar.

**S1.** Standardisera på `gap-2` för knappgrupper, `gap-3` för
sektioner.

---

## Q. Quick-actions och åtkomstvägar

- Skanna är åtkomlig via navbar OCH /scan/quick-länk på /pieces
- Återlämna är åtkomlig via /loans OCH /kiosk OCH /profile (Mina lån)
- Lägg-till-i-batch finns på /pieces, /kiosk, /scan/review

**Q1.** Detta är OK - genvägar är värdefulla. Men dokumentera i en
"Funktionsmatris" i CLAUDE.md så det inte tappas vid refaktoreringar.

---

## I. Inline-ikoner och emojis

Tidigare regel: inga emojis. Idag finns:

- ✓⚠✗ i inventerings-knappar (kvar - du har godkänt dessa)
- `+` som prefix i toppknappar (`+ Nytt utlån`)
- Inga andra emojis i koden

**I1.** Plus-prefix kollideras med K3-förslaget. Antingen alla
toppknappar med `+`, eller inga. Föreslår: inga (renare).

---

## Förslag på arbetsordning

Föreslagen prioritetsordning (lättast först, kosmetik sist):

1. **C** (confirm-dialoger - säkerhet)
2. **U** (tomma tillstånd - mycket användarfacing text)
3. **K** + **I** (knappterminologi - hänger ihop)
4. **P** (platsrendering - kräver makro, påverkar många vyer)
5. **D** (datumformat)
6. **F** + **S** (filter och spacing - en omgång samtidigt)
7. **H** (rubriknivåer)
8. **B** (badge-färger - kräver mest beslut)
9. **L** + **M** (listvyer och modaler - största förändringen)
10. **N** + **Q** (navbar - kan vänta tills resten är klart)

Säg vilken kategori du vill börja med, eller om du vill omprioritera.
