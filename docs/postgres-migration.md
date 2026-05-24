# Förberedelse för PostgreSQL-byte

Vi kör SQLite i MVP. Detta dokument beskriver vad som krävs av
kodbasen idag för att övergången till PostgreSQL ska vara enkel imorgon.

## När övergången blir aktuell

Triggers som motiverar bytet:

- Mer än ett tiotal parallella skribenter (osannolikt för detta projekt)
- Behov av riktigt bra fuzzy-search (`pg_trgm`, `unaccent`)
- Behov av vektorsökning för dubblettkoll (`pgvector`)
- Behov av starkare concurrency-garantier (t.ex. delade arbetspass)
- Skala över ~10000 noter där SQLite-FTS5 börjar bli klumpig

Tills någon av dessa inträffar: stanna på SQLite. Det är *enklare att
drifta*, *snabbare för enskild användare* och *enklare att backa upp*
(en fil).

## Vad vi gör redan nu för att förbereda

### Använd SQLModel, inte raw `sqlite3`

SQLModel är ett tunt lager ovanpå SQLAlchemy + Pydantic. Samma modeller
fungerar mot både SQLite och Postgres. Engine skapas via en URL:

```python
# SQLite (nu)
engine = create_engine("sqlite:///./data/notarkiv.db")

# Postgres (sen)
engine = create_engine("postgresql+psycopg://user:pass@host/db")
```

Inga modellfiler behöver ändras.

### Lägg SQLite-specifik kod bakom service-abstraktioner

**FTS5** är SQLite-only. Vi exponerar inte FTS5-syntax i route-koden.
Istället:

```python
# app/services/search.py
class SearchBackend(Protocol):
    async def search_pieces(self, query: str, filters: PieceFilters) -> list[Piece]: ...

class SQLiteFTS5Backend(SearchBackend):
    # MATCH-baserad sökning mot pieces_fts

class PostgresTrgmBackend(SearchBackend):
    # framtid: similarity()/ts_rank över tsvector
```

Route-koden tar `SearchBackend` via dependency injection. Bytet är
i ett ställe.

### Datatyper som beter sig olika

Undvik följande SQLite-specifika beteenden:

| Område                | SQLite                      | Postgres                | Vad vi gör              |
|-----------------------|-----------------------------|-------------------------|-------------------------|
| Datum/tid             | TEXT eller INTEGER          | TIMESTAMPTZ             | Använd alltid UTC. SQLAlchemy `DateTime(timezone=True)` |
| Bool                  | INTEGER 0/1                 | BOOLEAN                 | SQLModel `bool` - SQLAlchemy mappar transparent |
| JSON                  | TEXT                        | JSONB                   | SQLAlchemy `JSON`-typ ger samma API |
| Autoincrement-PK      | `INTEGER PRIMARY KEY`       | `BIGSERIAL`             | SQLModel default fungerar, men explicit `Field(default=None, primary_key=True)` |
| `INSERT OR IGNORE`    | SQLite-only                 | `ON CONFLICT DO NOTHING`| Använd SQLAlchemy `on_conflict_do_nothing` (postgres) eller `INSERT OR IGNORE` (sqlite) bakom helper |
| Case-insensitive match| `LIKE` är CI by default     | `LIKE` är CS, använd `ILIKE` | Använd `lower()`-anrop på båda sidor |
| Foreign key constraints| måste aktiveras explicit (`PRAGMA foreign_keys=ON`) | alltid på | Aktivera i SQLite-engine-init |

### Använd inte SQLite-specifika SQL-konstruktioner

Förbjudet i applikationskoden (kolla manuellt vid review):

- `WITHOUT ROWID`
- `STRICT`-tabeller (förbjudna på Postgres)
- `julianday()`, `strftime()` - använd Python `datetime` istället
- `glob()`, `regexp()` - använd Python-side filtrering eller `LIKE`
- Skuggning av kolumner med samma namn som funktioner
- `last_insert_rowid()` - använd SQLAlchemy `RETURNING` (fungerar på båda)

## Migrationsstrategi när bytet sker

1. Frys skrivningar (sätt appen i läsläge eller stäng kort)
2. Dumpa SQLite-data: `sqlite3 notarkiv.db .dump > dump.sql`
3. Skriv ett script (`scripts/migrate_to_postgres.py`) som:
   - Skapar Postgres-schema via `SQLModel.metadata.create_all()`
   - Läser från SQLite via SQLAlchemy och skriver till Postgres
   - Hanterar typkonverteringar (TEXT-datetime -> TIMESTAMPTZ etc.)
   - Skapar FTS-replacement (`tsvector`-kolumn + GIN-index)
4. Verifiera radantal per tabell
5. Byt `DATABASE_URL` i env, starta appen
6. Lägg till Alembic för framtida migrationer

Behåll SQLite-filen som arkiv ett tag.

## Tradeoffs i koden för portabilitet

Vi accepterar några små friktionspunkter idag för att framtida bytet
ska vara billigt:

- **Lite mer abstraktion i `services/search.py`** än om vi använde
  FTS5 direkt i routes
- **Datetime hanteras alltid som UTC** även om vi visar i lokal tid -
  två konverteringar istället för en
- **Ingen användning av SQLite-specifika prestandahacks** som
  `WITHOUT ROWID`

Detta är acceptabla kostnader. *Inte* acceptabelt: bygga om hela
arkitekturen "för säkerhets skull" innan vi vet om bytet ens behövs.

## Vad vi INTE förbereder för

- Andra databaser (MySQL, MongoDB, etc.) - SQLite -> Postgres är det
  enda spåret
- Sharding eller distribution - aldrig aktuellt för detta projekt
- Olika databaser per environment - dev och prod ska köra samma
