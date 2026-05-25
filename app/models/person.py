from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlmodel import Field, SQLModel


class ContributorRole(StrEnum):
    COMPOSER = "composer"
    ARRANGER = "arranger"
    LYRICIST = "lyricist"
    EDITOR = "editor"
    CONDUCTOR = "conductor"
    OTHER = "other"


class PersonLinkKind(StrEnum):
    WIKIPEDIA = "wikipedia"
    WIKIDATA = "wikidata"
    MUSICBRAINZ = "musicbrainz"
    IMSLP = "imslp"
    OFFICIAL = "official"
    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    INSTAGRAM = "instagram"
    OTHER = "other"


class Person(SQLModel, table=True):
    """En person knuten till en eller flera noter (kompositör, arrangör, textförfattare)."""

    __tablename__ = "people"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    sort_name: str = Field(index=True)
    # Partiella datum: bara year, year+month eller year+month+day
    birth_year: int | None = None
    birth_month: int | None = None
    birth_day: int | None = None
    death_year: int | None = None
    death_month: int | None = None
    death_day: int | None = None
    country: str | None = None  # ISO 3166-1 alpha-2, t.ex. SE, DE
    biography: str | None = None
    # Källan till biografin (för CC BY-SA-attribution när texten kommer
    # från Wikipedia). Sätts automatiskt när bio importeras från MB/Wiki.
    biography_source_url: str | None = None
    portrait_image_path: str | None = None  # Relativ sökväg under IMAGES_PATH
    # Källan till porträttet (för CC-attribution om bilden hämtats från Wikipedia/Wikidata).
    # Null för manuellt uppladdade bilder.
    portrait_source_url: str | None = None
    musicbrainz_artist_id: str | None = Field(default=None, index=True)
    wikidata_id: str | None = Field(default=None, index=True)
    biography_fetched_at: datetime | None = None
    portrait_fetched_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PersonLink(SQLModel, table=True):
    """Generisk länk knuten till en person.

    Ersätter tidigare wikipedia_url-fältet på Person. Vid MB-import
    skapas typiskt en länk med kind=wikipedia automatiskt.
    """

    __tablename__ = "person_links"

    id: int | None = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="people.id", index=True, ondelete="CASCADE")
    url: str
    kind: PersonLinkKind = Field(default=PersonLinkKind.OTHER, sa_type=String)
    label: str | None = None  # Frivillig display-text utöver kind
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PieceContributor(SQLModel, table=True):
    """Länk mellan en not och en person, med roll."""

    __tablename__ = "piece_contributors"

    id: int | None = Field(default=None, primary_key=True)
    piece_id: int = Field(foreign_key="pieces.id", index=True, ondelete="CASCADE")
    person_id: int = Field(foreign_key="people.id", index=True)
    role: ContributorRole = Field(sa_type=String)
    sort_order: int = Field(default=0)
