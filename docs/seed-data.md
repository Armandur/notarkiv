# Seed-data och databas-reset

Migrationer hanteras via `_ensure_column_guards()` i `app/db.py` -
en additions-dict per tabell som kör `ALTER TABLE ... ADD COLUMN`
idempotent vid varje `init_db()`. Nya tabeller skapas automatiskt av
`SQLModel.metadata.create_all()`.

`db reset` är destruktivt och får bara köras efter explicit
godkännande från användaren - även en till synes "testdatabas" kan
innehålla skarpa skanningar, biografier eller manuell metadata som
inte återskapas av seed.

Lokala snapshots i `snapshots/` (gitignored) är räddningsplanken vid
oavsiktlig nuke - de skapas av `scripts/backup.sh` och innehåller
sqlite3 .backup + bild-mappen.

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
  tags.yaml               # Liturgiska kategorier, voicing, ackompanjemang
  unit_kinds.yaml         # Typer av förvaringsenheter
  psalms/                 # Psalmböcker (1986 + 2003 års svenska psalmbok)
    svps_1986.yaml
    verbums_tillagg_2003.yaml
  pieces.yaml             # (valfritt) Provnoter för testning
  covers/                 # (valfritt) Bilder kopplade till pieces.yaml
```

`tags.yaml`, `unit_kinds.yaml` och `psalms/*.yaml` är pre-fyllda i
repot och seedas alltid av `seed_all()`. Övriga skapas av användaren
vid behov.

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
- name: Kyrkoåret
  kind: occasion
  description: Tider och söndagar i kyrkoåret
  sort_order: 0
- { name: Advent, kind: occasion, sort_order: 10, parent: Kyrkoåret }
- { name: Jul, kind: occasion, sort_order: 20, parent: Kyrkoåret }

- name: Kyrklig handling
  kind: occasion
  sort_order: 300
- { name: Begravning, kind: occasion, sort_order: 310, parent: Kyrklig handling }
- name: Vigsel
  kind: occasion
  sort_order: 350
  parent: Kyrklig handling
  aliases: [Bröllop]

- { name: SATB, kind: voicing, sort_order: 10 }
- { name: Piano, kind: accompaniment, sort_order: 20 }
```

`parent`-fältet pekar via taggnamn och kopplas i ett andra pass. `aliases`
lägger till synonymer i `tag_aliases`-tabellen som matchas i filterurlerna.

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

Vid nya kolumner: lägg in dem i `_ensure_column_guards()` och starta
om servern. Migrationen körs automatiskt.

Vid nya tabeller: definiera modellen, lägg till i `app/models/__init__.py`
och `app/db.py`-importen, starta om servern. `create_all()` skapar
tabellen.

`db reset` används bara när modellen genomgår större ombyggnad och
användaren explicit godkänner förlust av befintlig data. Ta alltid en
manuell snapshot till `snapshots/` först:

```bash
mkdir -p snapshots
sqlite3 data/notarkiv.db ".backup snapshots/innan-reset-$(date +%Y%m%d_%H%M).db"
```

## Förhållande till migrationsstrategin

`init_db()` är idempotent och kör i ordning:

1. `SQLModel.metadata.create_all()` - skapar nya tabeller som saknas
2. `_ensure_fts_objects()` - sätter upp FTS5 + triggers
3. `_ensure_column_guards()` - läser additions-dict, kollar
   `PRAGMA table_info(table)`, kör `ALTER TABLE ... ADD COLUMN` för
   varje kolumn som saknas.

Schemaändring i en SQLModel-klass innebär alltså:

- Helt ny tabell → ingen åtgärd behövs, `create_all()` fixar det
- Ny kolumn på befintlig tabell → lägg in i `additions`-dicten med
  kolumnnamn och SQL-typ
- Rename/drop/type change → manuell SQL eller om-skanning, prata med
  användaren först

Vid mer omfattande ändringar (många kolumner samtidigt, datamigration)
kan det vara värt att skriva ett engångs-script i `scripts/` och
committa det tillsammans med koden.

Alembic är inte införd och behövs inte heller - additions-dicten är
tillräcklig för en liten singel-tabell-per-vecka-tröskel.
