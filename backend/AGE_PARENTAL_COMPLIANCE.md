# Varsta, acord parental și age assurance — Astran Game

> **Nu** este o declarație de conformitate GDPR/DSA. Separă ce e implementat, ce cere configurare și ce cere validare juridică.

## 1. Probleme găsite (inițial + la auditul curent)
1. `POST /auth/register` accepta `age_category` de la client → oricine se declara `adult_18` fără nicio verificare (și primea acces 18+).
2. Conturile Google noi nu aveau `age_locked`: o singură alegere „adult" prin `PATCH /users/me` (ecranul de onboarding și Setările).
3. Verificări **fail-open**: `g.age_category == "adult_18" and user.age_category == "under_18"` — un utilizator cu câmpul lipsă/`null` trecea. `discover` filtra la fel.
4. Nepăzite: `GET /games/{id}/scene-models`, `GET /games/{id}/instances`, `GET /games/{id}/files|assets`; `/run` și `/client-bundle` aveau excepție pentru editori; `WebSocket /ws/games/...` nu reverifica vârsta (instanță deschisă înainte de retrogradare).
5. Orice minor putea marca un joc propriu `adult_18` (publicare de conținut 18+).
6. Nu exista dată de naștere, acord parental, jurnal de audit pentru vârstă.
7. **(audit curent)** Data declarată 16–17 sau 18+ nu era verificată: un copil putea declara 17 ani, își dădea singur consimțământul (`confirm_own_consent`) și folosea prieteni/DM/grupuri/shop/UGC fără nicio dovadă. Declararea 18+ ocolea la fel fluxul parental pentru funcțiile sociale.
8. **(audit curent)** `users.age_category`/`age_assurance` erau crezute brut de chat, DM, grupuri, realtime și prieteni: o scriere directă în DB (`adult_18`, `verified`) dădea acces adult și contact adult↔minor.
9. **(audit curent)** Pragul atestat era fix 18 și nelegat de sesiune; contul de părinte din panou era considerat adult doar după DOB declarat (un copil cu al doilea cont „adult" își putea aproba singur consimțământul).
10. **Preexistent, în arhiva primită:** `legal/manifest.json` referă `legal/privacy_policy/1.0.{ro,en}.txt` care **lipsesc**, iar hash-urile `terms_of_service/1.0.*.txt` **nu se potrivesc** cu manifestul → `legal_docs.get_registry()` (fail-closed) ar refuza pornirea serverului. Nu am inventat textul politicii; testele folosesc un director-fixture. **Trebuie rezolvat înainte de deploy.**

## 2. Arhitectura (după remedierea „16–17 declarat")

**Principiu:** data nașterii declarată **nu e dovadă de vârstă**. Ea decide doar *ce flux* se aplică; accesul la funcțiile care cer consimțământ digital se acordă **doar** după o dovadă verificată pe server: (a) atestare semnată de un provider de age assurance (prag ≥16), sau (b) acord parental **APROBAT** (doar pentru sub 16 și 16–17 declarat).

Sunt păstrate **separat** două lucruri:
- **categoria minor/adult** (`age_category`: `under_18` | `adult_18`) – `adult_18` doar cu atestare de prag **18**;
- **eligibilitatea pentru consimțământ digital** (`consent_eligibility`: `verified` | `parental` | `unverified`) – independentă de categorie. Un 17-ani verificat e eligibil **și** minor.

| Vârstă declarată | Stare fără dovadă | Ce poate face | Cum își deblochează contul |
|---|---|---|---|
| < 16 | `PARENTAL_CONSENT_REQUIRED` – **blocare totală** (`restricted`) | doar rutele de vârstă/parental/privacy/legal/auth | acord parental aprobat (verificat extern) |
| 16–17 | `AGE_VERIFICATION_REQUIRED` – **limitat** (`limited`) | explorare, joc (fără chat), setări, privacy, DSA, refund | verificare vârstă prag 16 **sau** acord parental |
| 18+ | `AGE_VERIFICATION_REQUIRED` – **limitat** | idem | verificare vârstă prag 18 |
| fără DOB | `AGE_INFO_REQUIRED` – limitat (blocat total după `ASTRAN_AGE_INFO_DEADLINE`) | idem | introduce DOB (o dată) + dovadă ca mai sus |

**Funcții care cer consimțământ (refuzate pe backend pentru cont limitat)** – `age_policy.CONSENT_FEATURE_PREFIXES`: prieteni, DM (+WebSocket), grupuri, căutare utilizatori, portofel, shop, haine, checkout; scriere pe `/api/games`, `/api/studio`, `/api/sandbox` (publicare/UGC, cu excepția `play`/`heartbeat`/`leave`); colaboratori/testeri. Chat-ul din joc, DM-ul și grupurile primesc categorie **necunoscută** → refuz. Poarta e în `get_current_user` (toate rutele autentificate) – nu în frontend.

**Încredere zero în `users.age_category` / `users.age_assurance`:**
- Atestarea `verified` e **sigilată** (HMAC-SHA256, cheie din `AGE_DATA_KEY`/`JWT_SECRET`) peste `user_id|status|prag` (`age_policy.sealed_assurance`, singurul constructor). Un `verified` scris direct în DB, copiat de la alt cont sau cu prag modificat **nu are efect**.
- Chat/DM/grupuri/realtime/prieteni nu mai citesc brut `age_category`: folosesc `age_policy.effective_category` / `derive_category_doc` (derivate din DOB + atestare sigilată + acord). Editarea manuală a categoriei în DB nu produce acces adult sau contact adult↔minor.
- Migrarea la pornire coboară `adult_18` cu „verified" **fără sigiliu valid** la `under_18`; seed-ul de operator (`ASTRAN_SEED_DEMO_USER=1`) e sigilat explicit.
- DOB: setat o singură dată (`age_locked`), criptat Fernet; schimbarea cere suport + reverificare (nu există flux API). Rescrierea `dob_enc` în DB nu dă acces fără atestarea sigilată.

**Age assurance (prag 16 sau 18):** serverul decide pragul permis (16–17 → 16; 18+ → 18; 18+ cu atestare de 18 eșuată → și 16). Pragul e salvat **în sesiunea de pe server**; callback-ul trebuie să ateste exact acel prag (altfel `threshold_mismatch`) – o atestare de 16 nu devine 18. Contract callback (definit în acest repo, semnat HMAC cu timestamp): `{"session_id","nonce","method"?,"threshold":16|18,"threshold_met":<bool strict>}` (formatul vechi `{"over_18": bool}` = prag 18). Se stochează doar rezultatul (prag, provider, metodă, dată, referință pseudonimă) – **fără documente de identitate/biometrie**. Limită: max 10 porniri/24h/utilizator, cooldown 30 s, sesiune cu TTL, single-use.

**Acord parental pentru 16–17:** `request_consent` e permis acum și pentru 16–17 declarat și neverificat (alternativă la verificarea vârstei); adultul declarat nu are această cale. Fluxul e cel de mai jos. Contul de părinte din panou trebuie să fie **adult dovedit** (atestare 18 sigilată), nu doar declarat – altfel un copil și-ar aproba singur consimțământul dintr-un al doilea cont.

**Acord parental (flux):** copil → `POST /api/parental/request` (email părinte) → email cu link (token 256 biți, doar HMAC în DB, expirare, single-use, în corpul POST) → părinte confirmă documentele (versiuni înregistrate) → `VERIFICATION_REQUIRED` → verificare externă a părintelui (atestare semnată) → `APPROVED`. **Link-ul + declarația NU aprobă.** Retragere: token de retragere single-use sau panou → `WITHDRAWN`: sub 16 → blocare totală; 16–17 → revine la `limited`; sesiunile WebSocket se deconectează (`on_withdrawn`). Audit: `age_audit_log` (doar insert, lanț de hash, pseudonime, detalii pe listă albă).

**Fail-closed:** fără provider de age assurance configurat nimeni nu poate fi verificat → utilizatorii 16+ rămân `limited`, nimeni nu are 18+. Fără verificator parental nimeni nu poate fi aprobat. Eșec/payload invalid/semnătură greșită/prag diferit/sesiune expirată sau reutilizată → fără acces, stare neschimbată (o atestare existentă nu e retrogradată de o pornire nouă).

## 3. Temeiuri juridice (PROPUNERI — validare juridică obligatorie)
| Prelucrare | Temei propus | Observație |
|---|---|---|
| Cont, joc, Studio, Shop, grupuri, chat/DM | art. 6(1)(b) contract | Sub 16: cont blocat până la acord parental (capacitate de exercițiu C. civ. — **nu** doar art. 8). Flag `ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL`. |
| Siguranța minorilor, moderare | art. 6(1)(f) + (c) (DSA) | |
| Evidența acordului parental | art. 6(1)(c)/7(1) | |
| Marketing | art. 6(1)(a) consimțământ | Nu se oferă sub 16; 16–17 își dau singuri acordul. |
| Age assurance 18+ | de stabilit (6(1)(a)/(f)) | Doar rezultatul (prag) se stochează. |

## 4. Provideri și variabile de mediu

**Provideri efectiv conectați: NICIUNUL.** Documentația proiectului nu numește niciun furnizor concret (Yoti, Veriff etc.) și nu există credențiale, endpoint-uri sau teste cu un furnizor real. Repo-ul conține doar **interfețe** (`AgeAssuranceProvider`, `ParentalVerifier`) și implementări generice „semnate" (`SignedWebhookProvider`, `SignedAttestationVerifier`) care verifică HMAC + timestamp. Un furnizor real necesită un **adaptor** care să producă contractul de mai sus, plus teste de integrare cu credențialele lui. Până atunci verificarea rămâne fail-closed.

| Variabilă | Rol |
|---|---|
| `ASTRAN_AGE_ASSURANCE_PROVIDER=signed_webhook` | activează adaptorul generic de age assurance |
| `ASTRAN_AGE_ASSURANCE_SECRET` | secret HMAC pentru callback (obligatoriu; fără el provider neconfigurat) |
| `ASTRAN_AGE_ASSURANCE_START_URL` | URL-ul de start al furnizorului (primește `session` și `threshold`) |
| `ASTRAN_AGE_ASSURANCE_NAME` | nume afișat/auditat |
| `ASTRAN_PARENTAL_VERIFIER=signed_attestation` | verificator al părintelui |
| `ASTRAN_PARENTAL_VERIFIER_SECRET`, `_START_URL`, `_METHOD` | secret HMAC, URL de start, metoda (ex. verificare plată) |
| `AGE_DATA_KEY` | cheie pentru DOB criptat **și** sigiliul atestărilor (fără ea derivă din `JWT_SECRET`; rotirea invalidează DOB și toate sigiliile → toți revin la `limited`) |
| `ASTRAN_PUBLIC_APP_URL` + SMTP (`psec.email_is_configured`) | link-uri în emailurile către părinți |
| `ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL` (implicit `1`) | blocare totală sub 16 |
| `ASTRAN_AGE_INFO_DEADLINE` (ISO) | după această dată, conturile fără DOB sunt blocate total |
| `ASTRAN_SEED_DEMO_USER`, `ASTRAN_DEMO_PASSWORD` | cont de operator (sigilat; **nu** e verificare reală) |

Alte cerințe: rol MongoDB cu doar `insert/find` pe `age_audit_log` + ancorarea periodică a ultimului hash; rută web/deep-link pentru `/parental/decision?token=` și `/parental/withdraw?token=`; (opțional) `Referrer-Policy: no-referrer`.

## 5. Teste

Rulare (din `backend/`): `python3 -m unittest astran_sandbox.test_age_system astran_sandbox.test_age_verification_gaps astran_sandbox.test_contact_policy astran_sandbox.test_group_age_protection astran_sandbox.test_chat_realtime astran_sandbox.test_dm_routes`.

`test_age_verification_gaps.py` (24 teste, cod real, `FakeDB` în memorie) – mapare pe cerințe: 1 (15 ani) `DeclaredAges.test_1…`; 2–3 (16/17) `test_2_3…`; 4 (18) `test_4…`; 5 `MinorTriesToAvoid.test_5…`; 6 `DobTamper`; 7 `ProviderBehaviour.test_7…`; 8 `test_8…` + `threshold_is_bound…`; 9–10 expirare/replay (sesiune assurance, token parental) `test_9_10…`, `ParentalForTeens.test_expired_request_token`, reutilizare token în `…relocks`; 11 retragere `ParentalForTeens…relocks`; 12 `DirectAdultAccess`; 13 `test_13_frontend_values…`; 14 `CategoryTamper`. Suita existentă `test_age_system.py` (58 teste) a fost actualizată: scenariile care codificau vechea regulă („16–17 își dau singuri consimțământul") acum afirmă regula nouă; fixture-urile folosesc atestări sigilate (`test_support_age.py`).

## 6. Probleme nerezolvate / limite (necesită intervenție manuală)
- **Nu există niciun provider conectat.** Până la configurarea unuia (și testarea lui reală) toți utilizatorii 16+ rămân `limited` – inclusiv conturile existente. Asta e consecința directă a cerinței fail-closed; planificați lansarea (comunicare către utilizatori, suport) înainte de deploy.
- Pentru aprobarea părinților e nevoie de un verificator parental real; fără el, calea parentală pentru 16–17 și sub 16 nu poate ajunge la `APPROVED`.
- Un părinte care folosește panoul trebuie să aibă atestare 18 (ceea ce cere provider de age assurance); calea prin link + verificare externă nu are această cerință.
- Contract callback definit local: furnizorii reali nu îl vorbesc nativ; adaptor + validare juridică/DPIA necesare.
- Cont legacy declarat adult: coborât la `under_18` până la atestare; un `verified` nesigilat (seed vechi, import manual) e coborât la migrare.
- Corectarea DOB nu are flux API (suport + reverificare). Dacă providerul atestă ≥16 pentru cineva care a declarat <16, contul rămâne blocat (conflict) până la intervenție manuală.
- Cine poate scrie în MongoDB **și** cunoaște `AGE_DATA_KEY`/`JWT_SECRET` poate falsifica sigilii – protejați cheia separat de DB.
- `consent`: consimțământul pentru marketing cere acum `consent_features_allowed`. Temeiurile juridice din §3 rămân propuneri.
- Ștergerea la cererea părintelui pornește automat doar dacă acordul a fost verificat cu o metodă reală; altfel `pending_manual_review`.
- Frontend: doar tipurile (`AgeState`, `AuthContext`, `startAssurance(threshold)`) au fost actualizate. **Nu există încă un ecran** pentru starea `AGE_VERIFICATION_REQUIRED` (butoane „Verifică vârsta" / „Cere acord parental"); API-ul răspunde 403 `age_verification_required` cu `next_action` și `parental_option`. Frontend-ul nu e o componentă de securitate, dar UX-ul trebuie adăugat.
- Testele HTTP (`test_age_http.py`) și serverul FastAPI complet nu au putut rula în acest mediu (fără `fastapi`/`motor`/`httpx`, fără rețea): `server.py` și rutele au fost compilate (`py_compile`) și verificate static (teste de wiring), nu exercitate prin HTTP.
- Suitele `test_real_jail`, `test_sandbox_security`, `test_scene_models` eșuează și pe arhiva originală (mediu: lipsă bwrap/dependențe) – nelegate de această modificare.
- Preexistent: `legal/manifest.json` referă `privacy_policy/1.0.*.txt` care lipsesc, iar hash-urile `terms_of_service` nu se potrivesc → serverul real refuză pornirea (fail-closed) până la rezolvare.

## 7. Serviciul central de entitlement-uri (`age_entitlement_service.py`)

Decizia per funcție se ia într-un singur loc, pe server, peste `age_policy.evaluate_access` (vârstă declarată ≠ dovadă; atestare sigilată; stare parentală). Poarta `get_current_user` apelează `ENT.check_request(user, path, method)` la **fiecare** cerere autentificată; regula veche „cont limitat ⇒ funcții care cer consimțământ" rămâne ca plasă de siguranță pentru căi nemapate. Chat-ul din joc și `/ws/dm` reevaluă `F_DM` (la conectare și la fiecare mesaj). Transferul Wallet apelează `ENT.transfer_allowed` înainte de orice mișcare de sold.

**Scopuri parentale separate** (`social`, `messaging`, `groups`, `purchases`, `publish`): date/retrase de părintele legat și verificat prin `POST /api/parental/children/{id}/scopes`; se păstrează data, scopul și versiunile politicii (`consents.scopes`, oglindit în `users.parental.scopes`). Sunt valide doar cât acordul curent e `APPROVED` și scopul poartă `consent_id`-ul curent → retragerea acordului le revocă automat (derivat la fiecare cerere, nu depinde de o ștergere).

| Funcție | sub 16 (acord) | 16–17 verificat | 18+ verificat |
|---|---|---|---|
| joc potrivit vârstei, blocare, wallet (vizualizare), shop (răsfoire) | da | da | da |
| friends/căutare · DM/chat · grupuri | scop `social` / `messaging` / `groups` | da | da |
| shop: cumpărare cu Astrans | scop `purchases` | da | da |
| bani reali (buy, Stripe, checkout) | scop `purchases` | **blocat implicit** (`ASTRAN_MINOR_REAL_MONEY_REQUIRES_PARENT=1`) | da |
| transfer între utilizatori | nu | nu | da (destinatar adult verificat, ne-suspendat) |
| publicare jocuri | scop `publish` | da | da |
| vânzare în shop / monetizare creator | nu | nu | da |
| conținut 18+ | nu | nu | da |

Sub 16 fără acord rămâne **blocat total** implicit (`ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL=1`). Cu flag-ul oprit primește doar jocul potrivit vârstei, nu funcții sociale/comerciale. Decizia de a opri flag-ul e juridică (capacitate de exercițiu, art. 8 GDPR) și nu a fost luată în cod.

`GET /api/age/entitlements` întoarce matricea (permis/motiv/pas următor) + starea UI (`not_verified`, `verification_pending`, `verified`, `verification_failed`, `parental_consent_required|pending|revoked`, `restricted`); ecranul `frontend/app/age-status.tsx` o afișează fără să calculeze nimic local.

Teste: `python3 -m unittest astran_sandbox.test_age_entitlements` (34 teste).
