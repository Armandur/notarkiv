"""Hjälpsidor läses från markdown i `docs/help/` så de kan uppdateras
utan kod-commit. En sida per roll + index. Inga roll-restriktioner -
alla auth:ade kan läsa vilken hjälpsida som helst."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.deps import require_auth
from app.models import User
from app.templates_setup import render

router = APIRouter(prefix="/help", tags=["help"])

HELP_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "help"

PAGES = {
    "index": "Hjälp",
    "reader": "Guide för musiker och körledare",
    "editor": "Guide för editor",
    "admin": "Guide för admin",
}


def _load(slug: str) -> str | None:
    path = HELP_DIR / f"{slug}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


@router.get("")
async def help_index(
    request: Request,
    user: User = Depends(require_auth),
) -> Response:
    content = _load("index")
    return render(
        request,
        "help/page.html",
        {
            "slug": "index",
            "title": PAGES["index"],
            "content": content or "",
            "pages": PAGES,
        },
        user=user,
    )


@router.get("/{slug}")
async def help_page(
    request: Request,
    slug: str,
    user: User = Depends(require_auth),
) -> Response:
    if slug not in PAGES:
        raise HTTPException(404)
    content = _load(slug)
    if content is None:
        raise HTTPException(404)
    return render(
        request,
        "help/page.html",
        {
            "slug": slug,
            "title": PAGES[slug],
            "content": content,
            "pages": PAGES,
        },
        user=user,
    )
