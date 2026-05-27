"""Smoke-test mot lokal körande server. Loggar in som admin och curlar
alla huvudsidor. Returnerar 0 om allt är OK, 1 om något returnerar 5xx
eller annan oväntad status.

Kör: uv run python scripts/smoke.py
Förutsätter att uvicorn lyssnar på 127.0.0.1:8766 och att INITIAL_ADMIN_*
matchar admin-lösenordet (eller satt via env CREDENTIALS_USER/PASSWORD).
"""

from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8766")
USER = os.environ.get("CREDENTIALS_USER", os.environ.get("INITIAL_ADMIN_USERNAME", "rasmus"))
PASSWORD = os.environ.get("CREDENTIALS_PASSWORD", os.environ.get("INITIAL_ADMIN_PASSWORD", "test1234"))

# Sidor som ska gå att GET:a som inloggad admin. Förväntad status 200 om
# inte annat anges (302 = redirect till login eller related, ej fel).
PAGES: list[tuple[str, set[int]]] = [
    ("/", {200, 302}),
    ("/pieces", {200}),
    ("/pieces?view=list", {200}),
    ("/people", {200}),
    ("/tags", {200}),
    ("/storage", {200}),
    ("/loans", {200}),
    ("/loans/cart", {200}),
    ("/scan", {200}),
    ("/scan/queue", {200}),
    ("/inventory", {200}),
    ("/kiosk", {200}),
    ("/pieces/qr-labels", {200}),
    ("/admin/users", {200}),
    ("/admin/settings", {200}),
    ("/admin/jobs", {200}),
]


def main() -> int:
    failures: list[str] = []
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10.0) as client:
        # Hämta login-sidan för CSRF + session-cookie
        r = client.get("/login")
        if r.status_code != 200:
            print(f"FAIL: kunde inte hämta /login (status {r.status_code})", file=sys.stderr)
            return 1

        # Extrahera csrf_token
        import re
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
        if not m:
            print("FAIL: hittade inget csrf_token i /login", file=sys.stderr)
            return 1
        csrf = m.group(1)

        r = client.post(
            "/login",
            data={"username": USER, "password": PASSWORD, "csrf_token": csrf},
        )
        if r.status_code not in (302, 303):
            print(f"FAIL: login misslyckades ({r.status_code}): {r.text[:200]}", file=sys.stderr)
            return 1

        for path, expected in PAGES:
            r = client.get(path)
            ok = r.status_code in expected
            mark = "OK " if ok else "FAIL"
            print(f"  {mark} {r.status_code} {path}")
            if not ok:
                failures.append(f"{path} → {r.status_code}")
                # Logga första felmeddelandet om det är 500
                if r.status_code >= 500:
                    print(f"       body: {r.text[:300]}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} fel:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nAlla {len(PAGES)} sidor OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
