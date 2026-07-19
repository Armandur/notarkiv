from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from app.models import (
    Piece,
    PieceImage,
    PiecePlacement,
    PieceTag,
    PsalmBook,
    PsalmEntry,
    StorageLocation,
    StorageUnit,
    Tag,
    UnitKind,
    User,
)
from app.templates_setup import render


def _kiosk_borrower(request: Request, session: Session) -> User | None:
    """Den autentiserade låntagaren för aktuell kiosk-session (via PIN).
    Timeout-logiken (rensa vid inaktivitet, touch-stämpel) delas med
    cart/kiosk-dependencies via deps.kiosk_borrower_id_if_active."""
    from app.deps import kiosk_borrower_id_if_active

    bid = kiosk_borrower_id_if_active(request)
    if not bid:
        return None
    return session.get(User, bid)


def _kiosk_context(request: Request, session: Session, kiosk) -> dict:
    """Hämta cart-batch + items + auth-state för kiosk-vyn.

    Cart är knuten till den autentiserade låntagaren (via PIN), inte till
    kiosken. Det gör att kioskdatorn kan delas av många användare
    utan att korgar blandas."""
    from app.models import Loan, LoanBatch, LoanBatchStatus
    from app.routes.loans import _enrich_loans, _get_or_create_cart, _unit_path

    borrower = _kiosk_borrower(request, session)
    cart = None
    cart_items: list = []
    active_solo: list = []
    active_batches: list = []

    kiosk_location = (
        session.get(StorageLocation, kiosk.location_id) if kiosk.location_id else None
    )
    allowed_unit_ids = _kiosk_location_unit_ids(session, kiosk.location_id)

    def _annotate(items: list[dict]) -> None:
        for it in items:
            it["in_kiosk_location"] = (
                allowed_unit_ids is None
                or (it["unit"] and it["unit"].id in allowed_unit_ids)
            )
            it["path"] = _unit_path(session, it["unit"]) if it["unit"] else "Okänd plats"

    if borrower:
        cart = _get_or_create_cart(session, borrower.id)
        cart_loans = session.exec(
            select(Loan).where(Loan.batch_id == cart.id).order_by(Loan.id)
        ).all()
        cart_items = _enrich_loans(session, cart_loans)

        from sqlalchemy import or_

        # "Mina lån" = där jag är låntagare ELLER där jag registrerade utlånet.
        # Senare täcker externa lån som låntagaren själv registrerat via kiosken.
        solo_loans = session.exec(
            select(Loan)
            .where(
                or_(
                    Loan.borrower_user_id == borrower.id,
                    Loan.registered_by == borrower.id,
                )
            )
            .where(Loan.returned_at.is_(None))
            .where(Loan.batch_id.is_(None))
        ).all()
        active_solo = _enrich_loans(session, solo_loans)
        _annotate(active_solo)

        # Aktiva batches: jag är låntagare eller jag skapade den
        batches = session.exec(
            select(LoanBatch)
            .where(
                or_(
                    LoanBatch.borrower_user_id == borrower.id,
                    LoanBatch.created_by == borrower.id,
                )
            )
            .where(LoanBatch.status == LoanBatchStatus.ACTIVE)
            .order_by(LoanBatch.borrowed_at.desc())
        ).all()
        for batch in batches:
            batch_loans = session.exec(
                select(Loan)
                .where(Loan.batch_id == batch.id)
                .where(Loan.returned_at.is_(None))
            ).all()
            enriched = _enrich_loans(session, batch_loans)
            _annotate(enriched)
            here_count = sum(1 for it in enriched if it["in_kiosk_location"])
            active_batches.append(
                {
                    "batch": batch,
                    "entries": enriched,
                    "total_remaining": len(enriched),
                    "here_count": here_count,
                }
            )

    # Aktivt inventeringsläge + progress
    from app.models import InventorySession
    from app.routes.kiosk_inventory import get_inventory_progress

    inv_progress = get_inventory_progress(session, kiosk)
    # Lista pågående inventeringar som editor kan välja att starta
    available_inventories = (
        list(
            session.exec(
                select(InventorySession)
                .where(InventorySession.ended_at.is_(None))
                .order_by(InventorySession.started_at.desc())
            ).all()
        )
        if borrower
        else []
    )

    return {
        "borrower": borrower,
        "kiosk": kiosk,
        "kiosk_location": kiosk_location,
        "cart": cart,
        "cart_items": cart_items,
        "cart_total": len(cart_items),
        "active_solo": active_solo,
        "active_batches": active_batches,
        "inventory_progress": inv_progress,
        "available_inventories": available_inventories,
    }


def _kiosk_location_unit_ids(session: Session, location_id: int | None) -> set[int] | None:
    """Hämta alla unit-IDn för en lagringsplats (inklusive nästlade barn).
    Returnerar None om location_id är None - det signalerar 'ingen filter'."""
    if not location_id:
        return None
    units = session.exec(
        select(StorageUnit).where(StorageUnit.location_id == location_id)
    ).all()
    return {u.id for u in units}


def _covers_by_piece(session: Session, piece_ids: list[int]) -> dict[int, PieceImage]:
    """Returnera mappning piece_id -> första bilden (sort_order asc) för en lista pieces."""
    if not piece_ids:
        return {}
    rows = session.exec(
        select(PieceImage)
        .where(PieceImage.piece_id.in_(piece_ids))
        .order_by(PieceImage.piece_id, PieceImage.sort_order, PieceImage.id)
    ).all()
    out: dict[int, PieceImage] = {}
    for img in rows:
        if img.piece_id not in out:
            out[img.piece_id] = img
    return out


async def _list_tree(request: Request, user: User, session: Session) -> Response:
    """Trädvy: lagringsplatser och enheter hierarkiskt, med antal placeringar."""
    from sqlalchemy import func as sqlf

    locations = session.exec(
        select(StorageLocation).order_by(StorageLocation.sort_order, StorageLocation.name)
    ).all()
    units = session.exec(
        select(StorageUnit)
        .where(StorageUnit.archived == False)  # noqa: E712
        .order_by(StorageUnit.sort_order, StorageUnit.name)
    ).all()

    # Räkna placeringar per unit
    counts: dict[int, int] = {}
    rows = session.exec(
        select(PiecePlacement.storage_unit_id, sqlf.count(PiecePlacement.id))
        .group_by(PiecePlacement.storage_unit_id)
    ).all()
    counts = dict(rows)

    # Bygg träd
    units_by_parent: dict[tuple[int, int | None], list[StorageUnit]] = {}
    for u in units:
        units_by_parent.setdefault((u.location_id, u.parent_id), []).append(u)

    def build_subtree(location_id: int, parent_id: int | None) -> list[dict]:
        children = units_by_parent.get((location_id, parent_id), [])
        result = []
        for c in children:
            subtree = build_subtree(location_id, c.id)
            # Aggregera count (egen + alla under)
            total = counts.get(c.id, 0) + sum(s["total"] for s in subtree)
            result.append({"unit": c, "own": counts.get(c.id, 0), "total": total, "children": subtree})
        return result

    tree = []
    for loc in locations:
        subtree = build_subtree(loc.id, None)
        total = sum(s["total"] for s in subtree)
        tree.append({"location": loc, "total": total, "units": subtree})

    return render(request, "pieces/list_tree.html", {"tree": tree, "view": "tree"}, user=user)


def _descendant_unit_ids(session: Session, root_id: int) -> list[int]:
    """Returnera root_id plus alla rekursiva barn-ID:n. In-memory BFS."""
    all_units = session.exec(select(StorageUnit.id, StorageUnit.parent_id)).all()
    children_map: dict[int, list[int]] = {}
    for uid, parent in all_units:
        if parent is not None:
            children_map.setdefault(parent, []).append(uid)

    result = [root_id]
    queue = [root_id]
    while queue:
        cur = queue.pop()
        for child in children_map.get(cur, []):
            result.append(child)
            queue.append(child)
    return result


def _descendant_tag_ids(session: Session, root_ids: set[int]) -> set[int]:
    """Returnera root_ids plus alla rekursiva barn-tagg-ID:n. In-memory BFS.

    Gör att ett val av en occasion-tagg (t.ex. kyrkoårstiden "Advent") även
    matchar noter taggade med dess helgdagar ("Första söndagen i advent")."""
    all_tags = session.exec(select(Tag.id, Tag.parent_id)).all()
    children_map: dict[int, list[int]] = {}
    for tid, parent in all_tags:
        if parent is not None:
            children_map.setdefault(parent, []).append(tid)

    result = set(root_ids)
    queue = list(root_ids)
    while queue:
        cur = queue.pop()
        for child in children_map.get(cur, []):
            if child not in result:
                result.add(child)
                queue.append(child)
    return result


def _resolve_filter_tag_ids(session: Session, tag_names: list[str]) -> list[int]:
    """Lös upp filtervärden (taggnamn eller alias) till tagg-ID:n inklusive
    rollup av hela hierarkin (parent -> barn). Delas av list-filtret och
    QR-etikettfiltret så de beter sig likadant."""
    from app.models import TagAlias

    tag_ids = set(session.exec(select(Tag.id).where(Tag.name.in_(tag_names))).all())
    tag_ids.update(
        session.exec(select(TagAlias.tag_id).where(TagAlias.name.in_(tag_names))).all()
    )
    if not tag_ids:
        return []
    return list(_descendant_tag_ids(session, tag_ids))


def _language_options(codes: list[str]) -> list[dict]:
    """Bygg lista med kod + display-namn (med flagga) för filterval."""
    from app.utils.languages import language_display, language_name_sv

    out = []
    for c in codes:
        out.append({"code": c, "label": language_display(c) or c, "name": language_name_sv(c)})
    out.sort(key=lambda r: r["name"])
    return out


def _filter_by_kind_tag_names(stmt, session, names, kind):
    valid = [n for n in names if n]
    if not valid:
        return stmt
    tag_ids = list(
        session.exec(
            select(Tag.id).where(Tag.kind == kind).where(Tag.name.in_(valid))
        ).all()
    )
    if not tag_ids:
        return stmt.where(Piece.id == -1)
    piece_ids = list(
        session.exec(
            select(PieceTag.piece_id)
            .where(PieceTag.tag_id.in_(tag_ids))
            .distinct()
        ).all()
    )
    if not piece_ids:
        return stmt.where(Piece.id == -1)
    return stmt.where(Piece.id.in_(piece_ids))


def _fts_match_query(q: str) -> str:
    """Bygg en säker MATCH-sträng för SQLite FTS5 av en fritextsökning.

    Tokeniserar på whitespace och omger varje token med dubbla citattecken
    (fras-token), med eventuella inbäddade citattecken dubblerade enligt
    FTS5:s escape-regel (" -> ""). Det gör att specialtecken som annars
    tolkas som FTS5-operatorer (t.ex. ledande "-" eller ett ensamt ")
    behandlas som vanlig text i stället för att ge syntaxfel.

    Sista token får ett "*" efter det stängande citattecknet för att
    bevara prefix-sökning på sista ordet, precis som tidigare beteende.

    Returnerar tom sträng om q saknar tokens efter strip - anropande kod
    ska då falla tillbaka på "ingen sökterm" (samma som tomt q)."""
    tokens = q.split()
    if not tokens:
        return ""
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    quoted[-1] += "*"
    return " ".join(quoted)


def _apply_filters(stmt, session, tags, voicings, accompaniments, languages, unit=None, include_subunits=False):
    if tags:
        # Matcha taggnamn/alias och rulla upp parent -> barn (kyrkoårstid -> helgdag).
        tag_ids = _resolve_filter_tag_ids(session, tags)
        if tag_ids:
            piece_ids_with_tag = list(
                session.exec(
                    select(PieceTag.piece_id)
                    .where(PieceTag.tag_id.in_(tag_ids))
                    .distinct()
                ).all()
            )
            if piece_ids_with_tag:
                stmt = stmt.where(Piece.id.in_(piece_ids_with_tag))
            else:
                stmt = stmt.where(Piece.id == -1)
        else:
            stmt = stmt.where(Piece.id == -1)
    if voicings:
        stmt = _filter_by_kind_tag_names(stmt, session, voicings, "voicing")
    if accompaniments:
        stmt = _filter_by_kind_tag_names(stmt, session, accompaniments, "accompaniment")
    if languages:
        valid = [l for l in languages if l]
        if valid:
            stmt = stmt.where(Piece.language.in_(valid))
    if unit:
        if include_subunits:
            unit_ids = _descendant_unit_ids(session, unit)
        else:
            unit_ids = [unit]
        piece_ids_in_unit = list(
            session.exec(
                select(PiecePlacement.piece_id)
                .where(PiecePlacement.storage_unit_id.in_(unit_ids))
                .distinct()
            ).all()
        )
        if piece_ids_in_unit:
            stmt = stmt.where(Piece.id.in_(piece_ids_in_unit))
        else:
            stmt = stmt.where(Piece.id == -1)
    return stmt


def _other_tags_grouped(session: Session) -> dict[str, list]:
    """Hämta alla taggar utom voicing/accompaniment grupperade per kind."""
    rows = session.exec(
        select(Tag)
        .where(Tag.kind.not_in(["voicing", "accompaniment"]))
        .order_by(Tag.kind, Tag.sort_order, Tag.name)
    ).all()
    out: dict[str, list] = {}
    for t in rows:
        out.setdefault(t.kind, []).append(t)
    return out


def _unit_picker_tree(session: Session) -> list[dict]:
    """Hierarkisk trädstruktur för unit-picker-modalen."""
    locations = session.exec(
        select(StorageLocation).order_by(StorageLocation.sort_order, StorageLocation.name)
    ).all()
    units = session.exec(
        select(StorageUnit)
        .where(StorageUnit.archived == False)  # noqa: E712
        .order_by(StorageUnit.sort_order, StorageUnit.name)
    ).all()
    kinds = {k.id: k.name for k in session.exec(select(UnitKind)).all()}

    units_by_parent: dict[tuple[int, int | None], list[StorageUnit]] = {}
    for u in units:
        units_by_parent.setdefault((u.location_id, u.parent_id), []).append(u)

    def build(location_id: int, parent_id: int | None, ancestors: list[str]) -> list[dict]:
        out = []
        for u in units_by_parent.get((location_id, parent_id), []):
            path = ancestors + [u.name]
            out.append(
                {
                    "unit": u,
                    "kind_name": kinds.get(u.kind_id),
                    "path": path,
                    "children": build(location_id, u.id, path),
                }
            )
        return out

    return [
        {"location": loc, "units": build(loc.id, None, [])}
        for loc in locations
    ]


def _find_psalm_title_matches(
    session: Session, title: str
) -> list[dict]:
    """Hitta PsalmEntries vars titel matchar piece-titeln case-insensitive.
    Returnerar lista av dicts med entry + bokens namn. Tom om ingen träff."""
    if not title:
        return []
    rows = session.exec(
        select(PsalmEntry, PsalmBook)
        .join(PsalmBook, PsalmBook.id == PsalmEntry.book_id)
        .where(PsalmEntry.title.ilike(title.strip()))
        .order_by(PsalmBook.sort_order, PsalmBook.name, PsalmEntry.number)
    ).all()
    return [{"entry": e, "book": b} for e, b in rows]


def _set_kind_tags(
    session: Session, piece_id: int, kind: str, tag_ids: list[int]
) -> None:
    """Sätt om PieceTag för en specifik tag-kind. Rensar befintliga och
    skapar nya. Tags med fel kind ignoreras."""
    # Hämta vilka existerande tag-ids på piecen som matchar denna kind
    existing = session.exec(
        select(PieceTag, Tag)
        .join(Tag, Tag.id == PieceTag.tag_id)
        .where(PieceTag.piece_id == piece_id)
        .where(Tag.kind == kind)
    ).all()
    for pt, _tag in existing:
        session.delete(pt)
    session.flush()
    for tag_id in tag_ids:
        t = session.get(Tag, tag_id)
        if t and t.kind == kind:
            session.add(PieceTag(piece_id=piece_id, tag_id=t.id))


def _kind_tags_by_piece(
    session: Session, piece_ids: list[int], kind: str
) -> dict[int, list[str]]:
    """Returnera dict piece_id -> lista av tag-namn för given kind,
    sorterade på Tag.sort_order. Tom dict om inga pieces."""
    if not piece_ids:
        return {}
    rows = session.exec(
        select(PieceTag.piece_id, Tag.name)
        .join(Tag, Tag.id == PieceTag.tag_id)
        .where(PieceTag.piece_id.in_(piece_ids))
        .where(Tag.kind == kind)
        .order_by(Tag.sort_order, Tag.name)
    ).all()
    out: dict[int, list[str]] = {}
    for piece_id, name in rows:
        out.setdefault(piece_id, []).append(name)
    return out


def _voicings_by_piece(session: Session, piece_ids: list[int]) -> dict[int, list[str]]:
    return _kind_tags_by_piece(session, piece_ids, "voicing")


def _accompaniments_by_piece(session: Session, piece_ids: list[int]) -> dict[int, list[str]]:
    return _kind_tags_by_piece(session, piece_ids, "accompaniment")


def _placement_summaries(session: Session, piece_ids: list[int]) -> dict[int, dict]:
    """Per piece: antal placeringar, summa fysiska exemplar, antal digitala
    placeringar, samt rader för tooltip (path + copies/digital-markör)."""
    if not piece_ids:
        return {}

    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units_by_id = {u.id: u for u in session.exec(select(StorageUnit)).all()}

    def path_for(unit_id: int) -> str:
        unit = units_by_id.get(unit_id)
        if not unit:
            return "?"
        parts = [unit.name]
        cur = unit
        while cur.parent_id:
            cur = units_by_id.get(cur.parent_id)
            if not cur:
                break
            parts.append(cur.name)
        loc = locations.get(unit.location_id)
        if loc:
            parts.append(loc.name)
        return " › ".join(reversed(parts))

    placements = session.exec(
        select(PiecePlacement).where(PiecePlacement.piece_id.in_(piece_ids))
    ).all()

    result: dict[int, dict] = {}
    for pl in placements:
        unit = units_by_id.get(pl.storage_unit_id)
        loc = locations.get(unit.location_id) if unit else None
        is_digital = bool(loc and loc.kind == "digital")

        s = result.setdefault(
            pl.piece_id,
            {"count": 0, "copies": 0, "digital": 0, "items": []},
        )
        s["count"] += 1
        if is_digital:
            s["digital"] += 1
        elif pl.copies:
            s["copies"] += pl.copies

        s["items"].append(
            {
                "path": path_for(pl.storage_unit_id),
                "copies": pl.copies,
                "digital": is_digital,
            }
        )

    for s in result.values():
        s["items"].sort(key=lambda i: i["path"])

    return result


def _unit_path_options(session: Session) -> list[dict]:
    """Flat lista av icke-arkiverade units med full sökväg som label."""
    locations = {loc.id: loc for loc in session.exec(select(StorageLocation)).all()}
    units = session.exec(
        select(StorageUnit).where(StorageUnit.archived == False)  # noqa: E712
    ).all()
    units_by_id = {u.id: u for u in units}

    def path_for(unit: StorageUnit) -> str:
        parts = [unit.name]
        cur = unit
        while cur.parent_id:
            cur = units_by_id.get(cur.parent_id)
            if not cur:
                break
            parts.append(cur.name)
        loc = locations.get(unit.location_id)
        if loc:
            parts.append(loc.name)
        return " › ".join(reversed(parts))

    options = []
    for u in units:
        loc = locations.get(u.location_id)
        if not loc:
            continue
        options.append({"id": u.id, "label": path_for(u), "location_kind": loc.kind})
    options.sort(key=lambda o: o["label"])
    return options


def _render_tag_area(request, session: Session, user: User, piece_id: int) -> Response:
    piece = session.get(Piece, piece_id)
    if not piece:
        raise HTTPException(404)
    active = session.exec(
        select(Tag)
        .join(PieceTag, PieceTag.tag_id == Tag.id)
        .where(PieceTag.piece_id == piece_id)
        .where(Tag.kind.not_in(["voicing", "accompaniment"]))
        .order_by(Tag.kind, Tag.sort_order, Tag.name)
    ).all()
    return render(
        request,
        "pieces/_tag_area.html",
        {"piece": piece, "active_tags": active},
        user=user,
    )
