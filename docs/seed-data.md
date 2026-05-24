# Seed-data och databas-reset

Fram tills projektet går i riktig produktion gäller: **vi gör inga
migrationer**. Vid schemaändringar nukar vi databasen och kör om
seed-skriptet med testdata. Detta sparar enormt mycket utvecklingstid
och är säkert eftersom inga "riktiga" produktionsdata finns ännu.

## Vad räknas som "riktig produktion"

Tröskeln är när någon faktiskt kör skarp registrering av sina noter
och förväntar sig att de finns kvar. Tills dess: ingen data är helig.

Operativt beslut: när användaren säger "nu går vi i prod" *eller* när
en backup av databasen tas första gången - då slutar nuke-strategin
vara OK och vi börjar bevara data.

## Verktyg

`app/cli.py` (Typer eller argparse) exponerar några kommandon för
livscykelhantering av databasen:

```bash
# Nuka och återskapa databasen (raderar SQLite-filen, kör init_db)
python -m app.cli db reset

# Bara seeda en existerande tom databas
python -m app.cli db seed

# Båda i ett kommando (vanligaste användningen)
python -m app.cli db reset --seed

# Skapa en admin-användare med givet användarnamn/lösenord
python -m app.cli users create-admin --username rasmus --password ...
```

## Seed-strukturen

Seed-data ligger i `seed_data/`:

```
seed_data/
  users.yaml              # Användare (admin, et al.) - valfri
  storage_locations.yaml  # Fysiska och digitala lagringsplatser - valfri
  tags.yaml               # Liturgiska kategorier, tillfällen - inkluderad
  unit_kinds.yaml         # Typer av förvaringsenheter - inkluderad
  pieces.yaml             # (valfritt) Provnoter för testning
  covers/                 # (valfritt) Bilder kopplade till pieces.yaml
```

`tags.yaml` och `unit_kinds.yaml` är pre-fyllda i repot och seedas
alltid. Övriga skapas av användaren vid behov.

YAML är förstaval eftersom det är trivialt att redigera för hand och
stödjer kommentarer. JSON funkar också om det visar sig krångligare.

### Exempel: `users.yaml`

```yaml
- username: rasmus
  email: rasmus@example.tld
  role: admin
  password: change-me-on-first-login   # bcryptas av seed-skriptet
  must_change_password: true
```

### Exempel: `storage_locations.yaml`

```yaml
- name: Notarkivet
  kind: physical
  units:
    - name: Hylla A
      kind: hylla
      children:
        - name: Pärm A1
          kind: parm
        - name: Pärm A2
          kind: parm
    - name: Skåp 2
      kind: skap

- name: SharePoint
  kind: digital
  units:
    - name: Körbiblioteket - Advent
      kind: sharepoint_mapp
      url: https://example.sharepoint.com/...
```

### Exempel: `tags.yaml`

```yaml
- name: advent
  kind: liturgical
  sort_order: 10
- name: jul
  kind: liturgical
  sort_order: 20
- name: begravning
  kind: occasion
  sort_order: 100
```

### Exempel: `unit_kinds.yaml`

```yaml
- hylla
- pärm
- låda
- mapp
```

Fler kinds skapas via UI:t (autocomplete-fältet i formuläret för ny enhet).

## Seed-skriptet

`app/cli.py db seed` läser YAML-filerna i bestämd ordning (`users` ->
`tags` -> `storage_locations` -> `pieces`), använder SQLModel-sessioner
för att skapa raderna, och hashar lösenord med bcrypt i farten.

Skriptet är idempotent där det är meningsfullt:
- Användare: skip om username finns
- Tags: skip om name finns
- Storage locations: skip om name finns
- Pieces: alltid skapa nya (eftersom de är test-data)

För testning där man vill kunna köra `seed` flera gånger utan
duplicering finns flaggan `--clear-pieces` som rensar `pieces`-tabellen
först.

## Hot-reload av seed-data under utveckling

Vid små schema- eller seed-ändringar:

```bash
# 1. Stoppa appen och worker
# 2. Reset+seed
python -m app.cli db reset --seed
# 3. Starta om
docker-compose up -d
```

Med Docker-volym för `data/notarkiv.db`: `--seed` raderar filen,
`init_db()` skapar nytt schema, seed-skriptet fyller med testdata.

## Förhållande till migrationsstrategin

I `docs/datamodell.md` beskrivs `init_db()` som idempotent med
ALTER-guards för nya kolumner. Det gäller **efter** vi gått i prod.

Före prod: vi struntar i ALTER-guards. När en kolumn läggs till,
ändras typ, eller döps om - skriv om SQLModel-klassen, kör reset+seed.

När prod-tröskeln passeras: börja behålla ALTER-guards för nya
kolumnersättningar. Dokumentera datumet i CHANGELOG.md eller motsv.

## När prod-tröskeln passeras

Checklista för övergång från "nuke-OK" till "bevara":

- [ ] Skapa en CHANGELOG.md och dokumentera datumet
- [ ] Stäng av eller fasa ut `db reset`-kommandot (eller lägg
      bakom `--force-yes-really`-flagga)
- [ ] Verifiera att backup (`litestream`) är konfigurerad och testad
- [ ] Sluta lägga schemaändringar i samma commit som koden -
      separera så de kan reviewas
- [ ] Börja använda ALTER-guards för nya kolumner istället för att
      ändra modellklassen rakt av
- [ ] Vid större ändringar: skriv migrationsscript som engångskörning,
      committa det, dokumentera

Vid det laget kan det vara värt att införa Alembic. Eller fortsätta
med handgjorda migrationer om de få ändringar som behövs gör det enkelt.
