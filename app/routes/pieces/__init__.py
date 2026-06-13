from app.routes.pieces._routers import router, public_router, kiosk_router
from app.routes.pieces import kiosk, listing, crud, metadata  # noqa: F401 - registrerar routes via import

# Helpers som andra moduler importerar direkt från app.routes.pieces (de var
# top-level i den gamla monoliten). Re-exporteras så importerna fortsätter funka.
from app.routes.pieces.helpers import (  # noqa: F401
    _accompaniments_by_piece,
    _covers_by_piece,
    _find_psalm_title_matches,
    _kiosk_location_unit_ids,
    _placement_summaries,
    _voicings_by_piece,
)

__all__ = ["router", "public_router", "kiosk_router"]
