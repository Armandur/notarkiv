# Backlog Export

## [P1][done] [notarkiv] Bug: /loans/{id}/return saknar ägarkontroll (IDOR)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
return_loan kräver bara require_cart_actor (vilken inloggad/PIN-låntagare som helst) och kontrollerar aldrig att lånet tillhör anroparen. Sekventiella loan_id syns i formulär på /loans. En reader/kioskbesökare kan POSTa /loans/<annans id>/return och markera annans lån återlämnat; om det var sista raden flippas hela batchen till returned utan fysisk återlämning. FIX: verifiera ägarskap (borrower_user_id/batch.created_by) eller kräv editor/admin.

- ID: `01KXVN1RJ89BRPY01Q9GXRSYMM`
- Type: bug
- Actor: ai:claude-code

---

## [P1][done] [notarkiv] SESSION_SECRET har publikt default utan prod-guard

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Sessionscookies signeras med default-strängen 'byt-detta-i-prod-tack' (även i .env.example, dvs i öppna repot) om SESSION_SECRET inte satts. Ingen startup-kontroll hindrar detta i produktion. En angripare som känner default-värdet kan själv signera en cookie {user_id: <admin>} och förfalska admin-session utan lösenord. FIX: vägra starta (eller generera+varna) när APP_ENV=production och secret == default. app/main.py:54 använder värdet.

- ID: `01KXVN1RHHR592WFM0Y5XSTKA4`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [notarkiv] Bug: kiosk adjust_piece saknar editor-behörighet

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
start/stop_inventory kräver require_kiosk_editor men adjust_piece kräver bara require_kiosk_session. Vem som helst vid en aktiverad (ej PIN-inloggad) kiosk kan POSTa /kiosk/inventory/adjust/{public_id} med valfritt delta och manipulera InventoryCheck (dölja saknade noter), checked_by blir null. FIX: kräv require_kiosk_editor även här.

- ID: `01KXVN1RR2KXM5007NM4FS45ZB`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [notarkiv] Kiosk-timeout kontrolleras inte på POST-routes (cart/return)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/). (flaggat av två subagenter).
kiosk_borrower_last_active-timeouten kollas bara i _kiosk_borrower() (GET-sidor i kiosk.py). require_cart_actor/require_kiosk_editor läser kiosk_borrower_id rakt ur sessionen utan timeout-koll, så POST /loans/cart/add, /cart/checkout och /loans/{id}/return lyckas långt efter att sessionen borde ha loggats ut på delad kiosk. SessionMiddleware saknar dessutom max_age (ärver 14 dagars default). FIX: centralisera timeout-koll i deps, sätt max_age.

- ID: `01KXVN1RQBW9JNQRDFXYJSE317`
- Type: bug
- Actor: ai:claude-code

---

## [P2][todo] [notarkiv] Bug: delete_image raderar delad bildfil utan referenskoll

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
delete_image tar bort filen från disk utan att kolla om andra PieceImage-rader delar samma image_path, till skillnad från delete_piece (crud.py:692-702) som gör det. Vid re-OCR/dubbletthantering kan två pieces peka på samma fil (scan.py återanvänder image_path); radering av bilden på ena piecen ger trasig 404-thumbnail på den andra. FIX: räkna referenser till image_path innan filradering, som i delete_piece.

- ID: `01KXVN1RPPW33QMP87GE0GMSV5`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [notarkiv] Bug: radering av user/psalmbook kraschar på FK-constraint (500)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
delete_user_action kollar bara self-delete, inte FK-refererande rader (loan_batches.created_by NOT NULL, scan_sessions.user_id, pieces.created_by) -> session.delete+commit ger IntegrityError och obehandlat 500. Samma mönster i delete_psalmbook (admin/psalmbooks.py:117-143) som bara kollar PiecePsalmRef men inte PsalmEntry.book_id. FIX: in-use-koll före radering med vänligt felmeddelande (som delete_unit_kind gör), eller blockera/omfördela.

- ID: `01KXVN1RNE26TEVMHWQQXV3D32`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [notarkiv] Bug: refresh_person_mb skriver över birth/death med None

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
refresh_person_mb (POST /people/{id}/refresh) sätter ovillkorligt birth/death_year/month/day = parse_partial_date(life-span), även när MB-artisten saknar life-span (blir None). Manuellt inmatade år (t.ex. 1685) raderas. Jämför enrich_person_from_mb (services/people.py:208-213) som korrekt bara fyller luckor. FIX: skriv bara över när MB har ett värde.

- ID: `01KXVN1RMFT0PSRENCJG636V3Q`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [notarkiv] Bug: OCR-jobb fastnar i extracting/enriching vid timeout

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
arq job_timeout=120 avbryter hängande coroutine med asyncio.CancelledError (ärver BaseException) som INTE fångas av except Exception. ScanSession.status uppdateras då aldrig till failed, error_message sätts inte, och _status.html visar 'Försök igen' bara vid status==failed -> evig spinner utan utväg. FIX: fånga CancelledError (eller BaseException) och sätt failed-status, eller sätt status i en finally/on_job-fail-hook.

- ID: `01KXVN1RKYWERF6Y4HWVD0GX5Y`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [notarkiv] Dubblerad dict-nyckel 'tags' i _ensure_column_guards tappar description-guard

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/). (verifierat: rad 141 och 147 har båda nyckeln 'tags').
Dict-literal med två 'tags'-nycklar -> sista vinner, så ALTER-guarden ('description','VARCHAR') körs aldrig. På en DB skapad innan Tag.description fanns kraschar seed/_seed_tags och all UPDATE av Tag.description med 'no such column: tags.description'. FIX: slå ihop till en 'tags': [('description','VARCHAR'), ('parent_id',...)].

- ID: `01KXVN1RKBNXGYZ3E8MHQQ69DQ`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [notarkiv] Lagrad XSS via markdown|safe i fritextfält

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
python-markdown sanerar inte inbäddad rå HTML och resultatet renderas med | safe på minst tre ställen: piece.notes (templates/pieces/detail.html:160), person.biography (templates/people/detail.html:46,50) och publisher.description (templates/publishers/detail.html:41,45). En editor sparar '<img src=x onerror=...>' -> exekveras i varje reader/editor/admin-session som öppnar sidan (sessionskapning/CSRF-token-stöld). Alla användare är personal men reader är mindre betrodd + defense-in-depth. FIX: sanera med bleach/markdown-bleach eller en tillåtlista efter markdown-rendering, innan | safe.

- ID: `01KXVN1RJTH2AJBFGCYP6785QG`
- Type: bug
- Actor: ai:claude-code

---

## [P2][todo] [notarkiv] Bug: drag-och-släpp i taggträdet blandar ihop kinds

Kodgranskning 2026-07-19 (drag-och-släpp-ordning, commit 289a0cd).

ROTORSAK: Root-taggar av alla fyra kinds (occasion/voicing/accompaniment/free) renderas i separata <table> men får alla data-parent-id="" eftersom parent_id är None. Både JS och backend definierar 'syskon' enbart som samma parent_id, så alla root-taggar hamnar i samma syskongrupp tvärs över kinds.

FÖLJDER:
- isSiblingTarget (list.html:292) returnerar true för rader i annan kind-tabell -> går att dra t.ex. occasion-roten 'Kyrkoåret' och släppa på voicing-taggen 'SATB'; insertBefore flyttar raden till fel tbody.
- siblingIds('') (list.html:262) samlar root-rader från alla fyra tabellerna via document.querySelectorAll -> persist skickar ids blandat över kinds.
- Backend-kontrollen reorder_tags (app/routes/tags.py:162) 'len({parent_id}) != 1' passerar eftersom alla är None ({None}, len 1), så sort_order skrivs om tvärs över kinds.

Nettoeffekt: att dra en root-tag kan flytta den till fel tabell och skriva om sort_order för orelaterade taggar av andra typer efter omladdning. Reordering av kyrkoår-BARNEN (huvudsyftet) fungerar, för de delar ett riktigt parent_id. Buggen träffar root-nivån. Befintligt test i tests/test_tags_hierarchy.py täcker inte mixed-kind-samma-None-förälder, så grönt test döljer det.

FIX: scopa syskonskap på kind också - lägg data-kind på raden, ta med i isSiblingTarget/siblingIds, och låt reorder_tags verifiera att alla taggar delar samma kind (inte bara samma parent_id). Lägg regressionstest för mixed-kind-fallet.

BERÖRDA STÄLLEN: app/routes/tags.py:162, app/templates/tags/list.html:262 och :292.

- ID: `01KXVM0PBXZTTRMFD1JMV29BF8`
- Type: bug
- Actor: ai:claude-code

---

## [P2][todo] [notarkiv] Verifierad återställningskörning av backup

MVP-klart-kriterium: backupen räknas inte som klar förrän en återställning testats manuellt av användaren.

- ID: `01KXVKHV462SDY7YHZMWZ2PW6N`
- Type: chore
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Testharnessen kör init_db/seed mot den riktiga dev-DB:n

Upptäckt under fixarbete 2026-07-19 (helkodsgranskningens uppföljning). Vid testkörning loggas 'Databas initialiserad: sqlite:///data/notarkiv.db' - dvs appens lifespan kör init_db() (och därmed seed_all() + FTS-setup) mot den RIKTIGA data/notarkiv.db i stället för test-engine:n. test_engine-fixturen monkeypatchar app.db.engine och overridar get_session, men lifespan/init_db verkar ändå träffa modul-engine skapad före patchen. Följd: (1) test-DB:n saknar pieces_fts (FTS-routes går inte att route-testa i harnessen - fick unit-testa _fts_match_query mot egen in-memory-tabell i stället), (2) tester muterar/seedar den riktiga dev-databasen vid varje körning. FIX: säkerställ att lifespan/init_db använder test-engine (t.ex. patcha innan app importeras, eller injicera engine), och skapa FTS-tabellen i test-setupen så FTS-routes kan testas. Verifiera genom att köra sviten och kontrollera att data/notarkiv.db inte rörs.

- ID: `01KXX1FQMMTY9KXTS0K096K3ZT`
- Type: bug
- Actor: ai:claude-code

---

## [P3][done] [notarkiv] Open redirect via return_to (protokoll-relativ //host)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Redirect-guarden return_to.startswith('/') släpper igenom '//evil.com'. edit_unit_form reflekterar return_to i hidden fält; offret submittar med giltig CSRF -> update_unit redirectar Location: //evil.com. FIX: avvisa värden som börjar med '//' (eller kräv en enda inledande '/' följt av icke-slash).

- ID: `01KXVN1S1ZJS7BKFWSE2CVJMQE`
- Type: bug
- Actor: ai:claude-code

---

## [P3][done] [notarkiv] extract_wikipedia_url godkänner spoofad domän via substräng

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Wikipedia-URL avgörs via 'wikipedia.org' in url (substräng) på community-redigerbar MB-data. 'https://wikipedia.org.evil.example/...' godkänns och sparas/visas som pålitlig Wikipedia-länk. FIX: parsa URL och kontrollera att hostnamnet slutar på .wikipedia.org.

- ID: `01KXVN1S1JBKQXRZ0X0293SCJD`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Filstorlekskoll efter full inläsning + ingen gräns på antal filer

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
MAX_UPLOAD_BYTES kollas efter await upload.read() (hela filen redan i minnet), och quick_scan_upload saknar tak på antal filer per request. 40+ bilder a 20MB i ett request buffrar hundratals MB samtidigt på delad Unraid-VM. FIX: strömma/kolla Content-Length, sätt tak på antal filer.

- ID: `01KXVN1S14XW7SMKH8VTK6PD0F`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Rate-limit på kiosk-PIN är TOCTOU (parallell brute-force)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
check_kiosk_attempts och record_kiosk_failure är separata lås-block, inte en atomär operation. 50 parallella POST mot kiosk-PIN läser alla count<MAX innan någon hunnit registrera fel -> alla 50 gissningar körs, vilket gör 4-siffrig PIN (10000 komb) brute-forcebar trots rate-limit. FIX: atomär räkna-och-kontrollera under ett lås.

- ID: `01KXVN1S0QHK66W3018PEVKXTX`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Race conditions i find/get-or-create (4 ställen)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
SELECT-sedan-INSERT utan låsning/IntegrityError-hantering på flera ställen (tvåpersonsflödet gör samtidiga skrivningar sannolika): find_or_create_publisher (services/publishers.py:64, unique -> 500 som tappar hela pieces-save), find_or_create_person (services/people.py:98, ingen unique -> tysta dubblettpersoner, bryter designbeslut 10), _get_or_create_cart (loans.py:72, dubbla carts), _ensure_favorites (lists.py:21, unique -> 500). FIX: gemensamt mönster - fånga IntegrityError och re-SELECT, eller unik constraint + upsert.

- ID: `01KXVN1S0C63JJ41VF1FK3H6WG`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Bug: delete_unit räknar arkiverade barn -> dödläge utan UI-utväg

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
has_children-kollen räknar även archived-enheter, men trädet/unit_detail döljer arkiverade och ingen mall exponerar dem. En förälder med ett arkiverat barn kan aldrig raderas ('ta bort under-enheter först') eftersom barnet inte syns/nås. FIX: exkludera archived i has_children, eller ge återställnings-/hård-radera-väg för arkiverade.

- ID: `01KXVN1RZXY5TFPMS3KFN5RRQH`
- Type: bug
- Actor: ai:claude-code

---

## [P3][done] [notarkiv] Bug: inventerings-check skrivbar på avslutad session

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
check_item saknar inv.ended_at-koll (som set_planned_unit rad 185 har). En öppen check-flik kan POSTa 'Hittad' efter att sessionen avslutats -> ny InventoryCheck + logg-rad i avslutad session, tvärtemot designbeslut 13. FIX: avvisa check om ended_at satt.

- ID: `01KXVN1RZAWBEW2K9ZX1GB1EW7`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Bug: flerstegscykel i tagghierarki döljer taggar

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
update_tag kollar bara att taggen inte blir sin egen förälder, inte hela anfaderkedjan. A(parent=null), B(parent=A); sätt A.parent=B -> cykel. _build_tree lägger varken A eller B i roots -> båda försvinner tyst ur /tags men finns kvar i DB och på noter. FIX: vandra anfaderkedjan och avvisa om target är ättling till taggen.

- ID: `01KXVN1RX75TTB9TSA7J5M8APY`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Bug: skanning kan raderas under bearbetning -> AttributeError i worker

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Efter OCR-anropet hämtas scan om via session.get() utan None-koll. /scan/queue listar även pending/extracting-skanningar och delete_scan (scan.py:308-327) kollar bara resulting_piece_id, inte status. Raderas en skanning mitt i jobbet -> scan.raw_response= på None -> AttributeError, ospårat jobbfel. FIX: None-guard i jobbet + blockera radering av skanningar under bearbetning.

- ID: `01KXVN1RWPQEYDVZ0YTRZP79XX`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Bug: extra scan-bilder orphanas vid 'lägg till placering på befintlig'

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Vid dubbletthantering flyttas bara scan.image_path (primärbilden) till befintlig piece; ScanSessionImage-rader (fram/bak/försättsblad från multi-bild, designbeslut 9) överförs aldrig -> filer+rader lever kvar okopplade. Eftersom resulting_piece_id sätts blockerar delete_scan framtida städning. FIX: överför/erbjud överföring av alla ScanSessionImage, eller städa dem.

- ID: `01KXVN1RW5B6XYZ6CYDE3TQKXH`
- Type: bug
- Actor: ai:claude-code

---

## [P3][done] [notarkiv] Bug: FTS5 MATCH-sökning kraschar (500) på specialtecken

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
q skickas rakt in i pieces_fts MATCH (q+'*') utan sanering, på tre routes (list/print/print_pdf). Söktermer med ledande '-' eller ensamt citattecken ger fts5 syntax error -> okontrollerad 500 för legitim input. FIX: escapa/citera termer eller try/except -> visa 'inga träffar'.

- ID: `01KXVN1RVF9G8YBCWMQ49NZMFD`
- Type: bug
- Actor: ai:claude-code

---

## [P3][done] [notarkiv] Bug: mark_not_found raderar lån utan status-validering

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
mark_not_found kollar bara batch_id, inte batch.status==PICKING eller picked_up_at is null. En gammal flik/bokmärke kan POSTa /loans/{id}/not-found för ett redan hämtat lån i en active batch -> raden raderas spårlöst, noten är fysiskt utlånad men saknar post, batchen kan fastna i active. FIX: validera status+picked_up_at, som UI:t redan gör.

- ID: `01KXVN1RTK708P0T97RQFD5XEK`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] TOCTOU-race i _cap_to_available -> överbokning

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
reserved-summan läses innan nya Loan-raden committas, utan lås/unik constraint. Två samtidiga 'Låna' på placering med copies=1 läser båda reserved=0 -> 2 reserverade av 1. FIX: SELECT ... FOR UPDATE-motsvarighet, eller kontrollera i en transaktion/retry.

- ID: `01KXVN1RSVQ0GK735SS57KNPC2`
- Type: bug
- Actor: ai:claude-code

---

## [P3][done] [notarkiv] Överbokning: copies=max(1,capped) kör över tillgänglighets-cap

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Vid uppdatering av cart/batch-rad sätts existing.copies = max(1, capped) (även rad 553, 1016). Golvet 1 kör över _cap_to_available som räknat 0 tillgängliga -> raden fastnar på 1 reserverat exemplar som inte går att rätta via UI. FIX: tillåt 0 = ta bort raden, eller separera 'ta bort' från capping.

- ID: `01KXVN1RS33RPQKQ5HTNNK1V8T`
- Type: bug
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Export av katalogen (CSV/PDF)

CSV/PDF-export av hela katalogen eller ett filtrerat urval, för delning utanför appen.

- ID: `01KXVKHV845N2TC45Q0198MSBS`
- Type: feature
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Täckningsöversikt (gap-analys) av repertoaren

Vy som visar vilka tillfällen/besättningar som har få eller inga noter ('Pingst: 0 noter, SSA: 2 noter') så körledaren ser var repertoaren är tunn. Ren läsanalys, inte inköpsförslag. Liten.

- ID: `01KXVKHV7BSSND4SFPTZE0WEWR`
- Type: feature
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Dagens/kommande tillfälle på startsidan

svk-API:t ger datum per helgdag. Visa 'Idag: Andra söndagen i advent' + noter som passar (via rollup på kyrkoårstiden). Gör tagghierarkin aktivt användbar. Liten-medel. Högst rekommenderad nytta/arbete enligt kodgranskning 2026-06-16.

- ID: `01KXVKHV70MZED8QZ9EWSKQH6T`
- Type: feature
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Tom Select-konsekvens i hela systemet

Byt ut kvarvarande native <select>/<datalist>-autocompletes mot Tom Select där lämpligt (storage-units, loans, inventory, admin-användarroll, tag-parents). Behåll native för korta on/off-listor (max 5 alternativ utan sök).

- ID: `01KXVKHV6KY1KJSN033WGE1JF0`
- Type: improvement
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Mobil-UI/UX-genomgång av alla vyer (360-400px)

Systematisk granskning i smal viewport: inget element ska orsaka horisontell scroll. Granska tabeller, filterformulär, modaler, navbarmeny, pill-makron, pre/code-block, pickup-listor, kiosk-vyn. Testa på riktig telefon, inte bara DevTools.

- ID: `01KXVKHV66RFS7WMX4E2426N3Q`
- Type: improvement
- Actor: ai:claude-code

---

## [P3][todo] [notarkiv] Interaktiv approval av låg-konfidens-personmatchningar

enrich_person_job auto-applicerar bara fuzz-score >= 88. Lägre konfidens lämnas oberikade utan spår. Förslag: spara kandidater under tröskeln (ny PersonCandidate-tabell/JSON-fält) och visa 'Möjliga MB-matchningar' med Använd-knapp per kandidat på Person-detalj. Plus retry-knapp för misslyckade jobb.

- ID: `01KXVKHV4KVTD4GJ0C4NNZBSZQ`
- Type: feature
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Login: användarnamn-enumerering via timing + saknad rate-limit

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
/login (användarnamn+lösenord) saknar helt rate-limit/lockout som kiosk-PIN-flödet har (check_kiosk_attempts anropas aldrig härifrån) -> obegränsad brute-force mot t.ex. 'admin'. Dessutom kortsluter 'not user or not verify_password' utan bcrypt när användaren saknas -> mätbar timingskillnad avslöjar existerande användarnamn. FIX: återanvänd rate-limit-modulen för /login; kör alltid en dummy-bcrypt vid okänt användarnamn. (Höjs lämpligen till P2 om appen exponeras utanför Tailscale.)

- ID: `01KXVN1S6NZ26VFJ4WRMF07C03`
- Type: bug
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Effektivitet: N+1-queries på flera vyer

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Flera hot paths kör N+1: /loans kör ~6 queries per batch via _enrich_loans (loans.py:196-221) -> ~240 queries vid 40 batchar; storage qr-labels kör unit_path (services/storage.py:10) med en session.get() per hierarkinivå per enhet -> ~800-1000 queries för 200 enheter; inventory check_pick_unit (inventory.py:263) en count-query per enhet; wikidata search_persons (wikidata.py:82) upp till 10 sekventiella await get_entity istället för asyncio.gather. FIX: batcha/gruppera queries, bygg träd i minnet, parallellisera WD-anrop.

- ID: `01KXVN1S5G2H8431S2BBQZ3KPJ`
- Type: improvement
- Actor: ai:claude-code

---

## [P4][done] [notarkiv] Tysta except i navbar-badges (ingen loggning)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
_active_inventory/_active_loans_count/_cart_count/_pending_review_count sväljer except Exception tyst (0/None) utan logg - bryter global CLAUDE.md 'Try/except som sväljer fel utan att logga'. Låst/korrupt DB (t.ex. under backup) ger tysta felaktiga badges utan logg-rad. FIX: logga via loguru före fallback.

- ID: `01KXVN1S46AZ2GTSF9M8J1F472`
- Type: chore
- Actor: ai:claude-code

---

## [P4][done] [notarkiv] Person.name.ilike escapar inte LIKE-wildcards

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
find_or_create_person kör Person.name.ilike(name) utan att escapa % och _. Ett namn som 'C_P_E_Bach' (understreck) matchar av misstag en snarlik befintlig person -> fel bidragsgivare kopplas. FIX: escapa wildcards eller använd exakt jämförelse (func.lower(name)==...).

- ID: `01KXVN1S3HS00D08HBET17QAHQ`
- Type: bug
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Tesseract-OCR blockerar worker-eventloopen

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
pytesseract.image_to_string är blockerande CPU-tung men anropas direkt i async-coroutine utan run_in_executor. Med max_jobs=4 fryser en stor Tesseract-körning hela eventloopen så inga andra jobb (enrich_person, parallella OCR) gör framsteg. FIX: await loop.run_in_executor(...) för Tesseract-anropet.

- ID: `01KXVN1S2Y155KNJXDVJC6VWR5`
- Type: improvement
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Listvyn trunkerar tyst (LIMIT 200 + FTS-300-före-filter)

Helkodsgranskning 2026-07-19 (7 parallella review-subagenter över hela app/).
Ofiltrerad /pieces hårdkodar LIMIT 200 utan paginering/indikering (CLAUDE.md anger 200-1000 noter) -> äldre noter osynliga tills man filtrerar. Dessutom hämtar fritextsök bara topp-300 FTS-kandidater INNAN tagg/plats/språk-filter (rad 82) -> en not som matchar både sök och filter kan falla bort utan varning. FIX: paginering + 'visar X av Y', och filtrera i SQL före limit.

- ID: `01KXVN1S2EJZAYEZJCE1YBJDWN`
- Type: bug
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Postgres-migration

När skalan eller behovet av bättre fuzzy-search motiverar det. Koden är redan skriven Postgres-redo (SQLModel, service-abstraktioner).

- ID: `01KXVKHVBNAPNWEXYA67J8D4QT`
- Type: chore
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Batch-skanningsläge (rytm-optimering)

Skanna not efter not i ren rytm utan navigering mellan submits. HTMX-bakgrundssubmit, nollställ, kameran direkt redo. Persistera placering över submits. Live-feed av redan skannade noter. Optimering av rytmen, inte ny funktionalitet.

- ID: `01KXVKHV8Y8KHB9MXSNJFJMG14`
- Type: feature
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Vidare uppdelning av crud.py

Teknisk skuld, inte funktion. crud.py är ~937 rader efter pieces.py-splitten. Kan delas vidare (MB-modal/enrich vs ren CRUD) när det blir naturligt.

- ID: `01KXVKHV8JN6KW9SFS3CH358J9`
- Type: chore
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Bibeltexter per helgdag

Lagra dagens texter per årgång från svk-API:t och koppla noter till dem ('noter som passar dagens evangelium'). Medel/stor, rör vid scope-gränsen - diskutera med användaren först.

- ID: `01KXVKHV7Q8XNW2B9V3CXPGGHN`
- Type: feature
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Telefon-som-skanner mot kiosk (tethering)

Kiosk utan handhållen QR-skanner: kiosken visar tether-QR/token, användaren skannar med mobil, telefonen klistras till kioskens session via kort-livs-token. Telefonens QR-skanningar pushas till kioskens session via polling/SSE. För både inventering och utlåning. Frikopplas vid timeout/klar.

- ID: `01KXVKHV5W5MQ68CSPARMNRQ1G`
- Type: feature
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] NFC/RFID-tagg-inloggning i kiosken

Komplement till PIN/QR med fysisk tagg/kort. Kräver hårdvara (USB NFC-läsare, ACR122U) + bryggtjänst som skickar UID som tangentbordsinput. 'nfc:<uid>'-mönster på samma input-fält, NFC-UID-lista per User (många-till-en).

- ID: `01KXVKHV5G2HYYG779Z1ARPKVT`
- Type: feature
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Klick-på-vit vitbalans-kalibrering i preview-modal

Vitbalans-knapp i preview-modalen: klicka en punkt som ska vara vit, beräkna offset per RGB-kanal (255 - clicked_value) och applicera på hela bilden. För noter med starkt färgcast som auto-levels inte fångar. Canvas-click-handler + koordinatöversättning. UX-tungt på mobil.

- ID: `01KXVKHV4YS2XZJ36XNAW4F08V`
- Type: feature
- Actor: ai:claude-code

---

## [P4][todo] [notarkiv] Hybrid-OCR: Tesseract OCR + Claude för strukturering

Faller tillbaka till claude_vision tills implementerad. Kan bli intressant om kostnad blir ett problem.

- ID: `01KXVKHV3MWPD7WV16R4D1DSJJ`
- Type: feature
- Actor: ai:claude-code

---

## [P5][todo] [notarkiv] MS Graph API-integration för SharePoint

Bläddra SharePoint direkt. OBS: 'Aktivt utanför scope' i CLAUDE.md/ROADMAP - diskutera före bygge.

- ID: `01KXVKHVAXWQXR2HDK7JFGJB0K`
- Type: feature
- Actor: ai:claude-code

---

## [P5][todo] [notarkiv] Filuppladdning för digitala noter (PDF/MusicXML/MP3)

Om behovet visar sig. OBS: designbeslut 4 i CLAUDE.md säger medvetet nej idag - diskutera scope före bygge.

- ID: `01KXVKHVADK509ZR1ZXB1K7MFA`
- Type: feature
- Actor: ai:claude-code

---

## [P5][todo] [notarkiv] IMSLP-integration för fri sheet music

Komplement till MusicBrainz för fri sheet music där det finns.

- ID: `01KXVKHVA0AYVRJ4FTCEGXQC0T`
- Type: feature
- Actor: ai:claude-code

---

## [P5][todo] [notarkiv] Offline-stöd via PWA

Cacha skanningar lokalt på mobilen och synca när uppkoppling finns.

- ID: `01KXVKHV9MZHXC97J4D1JA48SM`
- Type: feature
- Actor: ai:claude-code

---

## [P5][todo] [notarkiv] Framförandehistorik

Spara vilka noter som använts vid vilka gudstjänster/konserter, generera statistik. OBS: ligger i 'INTE detta projekt'-listan i CLAUDE.md - diskutera scope före bygge.

- ID: `01KXVKHV99GT2N72FCET6HJKE2`
- Type: feature
- Actor: ai:claude-code

---

