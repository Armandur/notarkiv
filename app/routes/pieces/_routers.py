from fastapi import APIRouter

router = APIRouter(prefix="/pieces", tags=["pieces"])

# Separat router för /p/{public_id} - landningssidan QR-koder pekar på.
public_router = APIRouter(tags=["pieces"])
# Separat router för kiosk-vyn - skannerinput -> piece.
kiosk_router = APIRouter(prefix="/kiosk", tags=["pieces"])
