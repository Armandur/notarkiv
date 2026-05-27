# Notarkiv

Webbapp för att dokumentera och söka i ett fysiskt notförråd genom att
skanna omslag med mobilkameran. Tänkt för kyrklig körverksamhet men
generell nog för andra kontexter.

## Vad det löser

Vi har ett stort notförråd som är oindexerat och svårt att överblicka.
Körledare letar typiskt efter "något lämpligt för advent, SAB-besättning,
medelsvårt". Det går inte att svara på utan en sökbar katalog.

Lösningen: skanna varje omslag, låt en OCR/vision-modell extrahera
metadata (titel, kompositör, besättning, etc.), människan granskar och
kompletterar, posten sparas i en databas. Sökning och filtrering sker
sedan via webben.

## Status

MVP är funktionsmässigt komplett: skanning med OCR och MusicBrainz,
granskning, sökning, lagringsplatser, två-personers-flöde, inventerings-
tillfällen med per-enhet checklista, admin (användare och inställningar),
multi-bilder per not med rotation, manuell skapelse, redigering,
borttagning, taggar, dubblettkoll, kompositörer som egna entiteter
med biografi från Wikipedia och porträtt från MusicBrainz/Wikidata,
placeringshantering, psalmreferenser mot 1986 + 2003 års svenska
psalmbok, utlåning (både enskilt och grupperat med kundvagn/plockläge),
PDF-utskrift av notkatalog och plocklistor. Återstår innan prod-deploy:
backup via litestream, hybrid-OCR och eventuellt fler V2-finputs. Se
`ROADMAP.md` för fullständig status.

## Tänkt drift

- Docker-container på Unraid hemma
- SQLite med `litestream`-replikering till offsite-bucket (t.ex. Backblaze B2)
- Caddy som reverse proxy
- Åtkomst internt via Tailscale eller hemnätet

## Användarroller

- **Läsare**: hela arbetslaget. Kan söka, bläddra, se placeringar.
- **Redigerare**: ett fåtal. Kan skanna in, redigera metadata, hantera
  förvaringsplatser.

## Tekniska beslut i korthet

Se `CLAUDE.md` för utförlig motivering.

- FastAPI + SQLite (via SQLModel) + Jinja2 + HTMX + Bootstrap 5
- Claude Vision (claude-haiku-4-5) som default-OCR med Tesseract som
  fallback bakom ett gemensamt interface
- MusicBrainz-berikning av kanoniska metadata efter OCR
- arq + Redis för bakgrundsjobb (OCR, MusicBrainz-lookup)
- Strukturerade förvaringsplatser (rum + nästlade enheter), samma modell
  för fysisk och digital lagring
- Egen användarhantering med användarnamn/lösenord, sessionscookies
- Kodbasen designas så att SQLite kan bytas mot PostgreSQL utan
  arkitekturändringar
