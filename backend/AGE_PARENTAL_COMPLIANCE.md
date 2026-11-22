# Varsta, acord parental și age assurance — Astran Game

> **Nu** este o declarație de conformitate GDPR/DSA. Separă ce e implementat, ce cere configurare și ce cere validare juridică.

## 1. Probleme găsite în codul inițial
1. `POST /auth/register` accepta `age_category` de la client → oricine se declara `adult_18` fără nicio verificare (și primea acces 18+).
2. Conturile Google noi nu aveau `age_locked`: o singură alegere „adult" prin `PATCH /users/me` (ecranul de onboarding și Setările).
3. Verificări **fail-open**: `g.age_category == "adult_18" and user.age_category == "under_18"` — un utilizator cu câmpul lipsă/`null` trecea. `discover` filtra la fel.
4. Nepăzite: `GET /games/{id}/scene-models`, `GET /games/{id}/instances`, `GET /games/{id}/files|assets`; `/run` și `/client-bundle` aveau excepție pentru editori; `WebSocket /ws/games/...` nu reverifica vârsta (instanță deschisă înainte de retrogradare).
5. Orice minor putea marca un joc propriu `adult_18` (publicare de conținut 18+).
6. Nu exista dată de naștere, acord parental, jurnal de audit pentru vârstă.
7. **Preexistent, în arhiva primită:** `legal/manifest.json` referă `legal/privacy_policy/1.0.{ro,en}.txt` care **lipsesc**, iar hash-urile `terms_of_service/1.0.*.txt` **nu se potrivesc** cu manifestul → `legal_docs.get_registry()` (fail-closed) ar refuza pornirea serverului. Nu am inventat textul politicii; testele folosesc un director-fixture. **Trebuie rezolvat înainte de deploy.**

## 2. Design (pe scurt)
- Clientul trimite doar `date_of_birth` (ISO `AAAA-LL-ZZ`), validat strict pe server (`age_policy.parse_dob`): format, calendar, viitor, >120 ani. DOB se păstrează **criptat** (Fernet, `AGE_DATA_KEY`); în loguri/audit nu apare niciodată. Bandă recalculată la fiecare cerere (`under_16`, `16_17`, `18_plus`).
- `users.age_category` rămâne pentru compatibilitate (chat, DM, realtime îl citesc din DB) dar e **derivat**: devine `adult_18` doar prin `age_service.apply_assurance_result` (atestare semnată de provider). Test static verifică asta.
- Acces 18+ = `age_policy.can_access_adult_content`: assurance verificat **și** compatibil cu DOB **și** cont ne-restrâns. 16–17 ani: nu pot porni assurance, rămân minori (DSA) dar își dau singuri consimțământul (art. 8).
- Poartă centrală în `get_current_user`: cont sub 16 fără acord aprobat (sau fără vârstă după `ASTRAN_AGE_INFO_DEADLINE`) → 403 `account_restricted` pe orice rută în afara `/api/age|parental|auth/me|consent|privacy|legal`. Idem pe WebSocket (realtime, DM).
- Acord parental: `PENDING → VERIFICATION_REQUIRED → APPROVED`; `REJECTED`; `WITHDRAWN`. **Link-ul din email + declarație NU aprobă.** `APPROVED` doar după atestare semnată de un provider de verificare parentală configurat. Fără provider: rămâne `PARENTAL_VERIFICATION_REQUIRED`.
- Tokenuri: 256 biți, doar HMAC în DB, expirare, single-use cu consum atomic, tokenul în corpul POST. Panou parental: cont logat, email verificat, adult, **și** link creat din token/decizie; fiecare acțiune re-autorizată (404 la mismatch).
- Audit: `age_audit_log`, doar insert, lanț de hash (tamper-evident), pseudonime HMAC, detalii pe listă albă.
- Migrare (la pornire, idempotentă): `adult_18` fără assurance → `under_18` (+`legacy_declared_age_category`), valori necunoscute → `under_18`, conturi fără DOB → `age_info_required`. Nu șterge nimic, nu inventează date.

## 3. Temeiuri juridice (PROPUNERI — validare juridică obligatorie)
| Prelucrare | Temei propus | Observație |
|---|---|---|
| Cont, joc, Studio, Shop, grupuri, chat/DM | art. 6(1)(b) contract | Sub 16: cont blocat până la acord parental (capacitate de exercițiu C. civ. — **nu** doar art. 8). Flag `ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL`. |
| Siguranța minorilor, moderare | art. 6(1)(f) + (c) (DSA) | |
| Evidența acordului parental | art. 6(1)(c)/7(1) | |
| Marketing | art. 6(1)(a) consimțământ | Nu se oferă sub 16; 16–17 își dau singuri acordul. |
| Age assurance 18+ | de stabilit (6(1)(a)/(f)) | Doar rezultatul (prag) se stochează. |

## 4. Ce necesită configurare externă (nimic nu e conectat în repo)
- Provider verificare parentală (`ASTRAN_PARENTAL_VERIFIER*`) — fără el nimeni sub 16 nu poate fi aprobat.
- Provider age assurance (`ASTRAN_AGE_ASSURANCE*`) — fără el **nimeni** nu are acces 18+.
- SMTP (`psec.email_is_configured`) și `ASTRAN_PUBLIC_APP_URL` pentru emailurile către părinți. Fără ele cererea se înregistrează dar răspunsul spune `delivery: not_configured`.
- `AGE_DATA_KEY` (altfel cheia derivă din `JWT_SECRET`; rotirea lui fără `AGE_DATA_KEY` face DOB nedecriptabil → cont „fără vârstă", fail-closed).
- Rol MongoDB cu doar `insert/find` pe `age_audit_log`; ancorarea periodică a ultimului hash în afara DB.
- Deep-link/route web pentru `/parental/decision?token=` și `/parental/withdraw?token=` în aplicația Expo; (opțional) `Referrer-Policy: no-referrer`.

## 5. Probleme nerezolvate / limite
- Data nașterii e **declarată**: un copil poate minți „am 17 ani" ca să evite acordul parental. Fără verificare reală a vârstei pentru toți utilizatorii nu există remediu complet.
- Un email controlat de copil poate trece de pasul „link"; de aceea `APPROVED` cere verificarea externă. Calitatea ei depinde de provider (nu e inclus).
- Cont legacy declarat adult: coborât la `under_18` până la assurance; deținătorii de jocuri 18+ nu le mai pot juca/rula (administrare permisă).
- Corectarea DOB (greșeală) nu are flux API: doar suport + verificare (decizie intenționată).
- Utilizatorul care împlinește 16 ani cu acord parental retras/respins trebuie să confirme singur (`/api/age/own-consent`) — implementat, dar textul/UX de validat juridic.
- Ștergerea la cererea părintelui pornește automat doar dacă acordul a fost verificat cu o metodă reală; altfel `pending_manual_review`.
- Shop/Marketplace: codul nu are conținut marcat 18+ (verificat prin grep); dacă se introduce, trebuie să folosească `age_guard`.
- Frontend: verificat doar sintactic (`transpileModule`); nerulat/netestat în Expo, fără typecheck (lipsesc `node_modules`).
- Testele HTTP (`test_age_http.py`) nu au rulat (fără fastapi/httpx). Handler-ele FastAPI din `age_routes.py` și patch-urile din `server.py` au fost doar compilate (`py_compile`) + verificate static.
