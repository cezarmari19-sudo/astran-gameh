# Corecturi aplicate (18 bug-uri + performanță)

> **Nu am putut rula aplicația** (fără MongoDB/FastAPI/Expo în sandbox). Verificat prin: `compileall`,
> importul real al `server.py` și al modulelor de rute modificate (cu dependențele înlocuite), 201 teste
> `unittest` (cele 10 suite care rulează fără dependențe externe, inclusiv teste noi cu un DB fals în
> memorie pentru logica de bani), verificări statice ale invarianților și transpilarea TypeScript a
> fișierelor modificate. `test_real_jail` și `test_sandbox_security` cer `pytest` și nu au putut rula
> (la fel și pe arhiva originală). **Rulează suita reală și un smoke-test pe un MongoDB de staging înainte de deploy.**

Teste noi: `astran_sandbox/test_money.py`, `test_ratelimit.py`, `test_fixes_static.py`
(`cd backend && python -m unittest astran_sandbox.test_money astran_sandbox.test_ratelimit astran_sandbox.test_fixes_static`).

## Bani și integritate
| # | Ce era | Ce s-a făcut |
|---|---|---|
| 12 | Debit/credit/ledger neatomice | Modul nou `astran_sandbox/money.py`: jurnal `astran_money_journal` scris ÎNAINTE de orice mutare + pași idempotenți per-document (markere în `users._money_ops`) + task de recuperare (la 60 s) care duce la capăt operațiile rămase la jumătate. Folosit în transfer, shop/buy, clothes/buy, group create și `/wallet/buy`. La group create, `_generate_token` rulează înainte de debit. Merge pe MongoDB standalone (fără tranzacții). |
| 13 | `except Exception` → `already_owned` | Se prinde doar `DuplicateKeyError`; rezervarea rândului de purchase poartă `op_id`, deci un rollback nu poate șterge achiziția altei cereri. |
| 14 | `payment_intent` folosibil de oricine | `StripeProvider.verify` cere `metadata.astran_user_id/package_id`; intent-urile se creează doar prin noul `POST /wallet/stripe/intent`. `pi_...` validat înainte de a intra în URL. |
| 15 | Editor „view” putea schimba `target` | `save_files` compară `(source, target)`. |
| 9 | Taxă 0 sub 20 Astrans | Rotunjire în sus cu `Fraction`; `net < 1` e respins (suma 1 nu mai poate fi transferată). |

## Securitate
| # | Ce s-a făcut |
|---|---|
| 16 | `age_category` nu mai poate fi ridicat la 18+ prin `PATCH /users/me` după ce a fost setat (`age_locked`); coborârea e permisă. Conturile cu parolă sunt blocate la pornire (backfill); conturile Google au o singură alegere (onboarding). **Limită:** fără verificare reală a vârstei, valoarea de la înregistrare rămâne o declarație. |
| 17 | Chat-ul de grup: filtrul de chat pentru minori (`sender_adult` din DB, ca în `chat_core.text_for`), blocări în ambele direcții, mute global; fail-closed (503) dacă filtrul nu se poate citi. Mesajele vechi se filtrează pentru toți. |
| 18 | Rate limit: `/auth/register` (10/oră/IP), `/auth/login` (30/10 min/IP + blocare 15 min după 5 eșecuri per IP+email, în Mongo), `/reports` (10/oră/user). Rapoarte: lungimi maxime, ținta trebuie să existe și să fie accesibilă. În spatele unui proxy setează `ASTRAN_TRUST_PROXY=1` (sau `RENDER`) și, dacă e cazul, `ASTRAN_TRUSTED_PROXY_HOPS`. Limitatorul în memorie e **per proces**. |
| 8 | Conturile Google (fără parolă) pot porni recover/reset (codul de email rămâne obligatoriu); `setup` cere o sesiune creată în ultimele 15 min. `has_password` expus în `/auth/me`; UI-ul ascunde câmpul de parolă. |

## Funcționale
| # | Ce s-a făcut |
|---|---|
| 1 | Backend: `player_character_source` (create/patch/răspuns, cu inferență pentru jocuri vechi), validare că modelul e al owner-ului/editorului, endpoint nou `GET /games/{id}/player-character` (autorizare = cea a jocului). Frontend: `play/[id].tsx` și `studio/play/[id].tsx` îl folosesc. |
| 2 | `wallet.tsx`: `shop_purchase`, `clothes_purchase`, `group_create` sunt cheltuieli (roșu, minus). |
| 3 | `script` din create/PATCH devine `main.lua` în `game_scripts` (validat; script gol nu șterge codul; editorii restricți au nevoie de acces la `main.lua`). |
| 4 | Ștergerea jocurilor contului demo rulează doar când demo-ul NU e activ. |
| 5 | „Playing” se calculează din `instance_players.last_seen` (60 s), jucătorii expirați se șterg la join/list, `total_plays` crește doar la intrări noi. |
| 6 | Cine a plătit păstrează accesul (`owns` înainte de `visible`) la detaliu și la `buy`. |
| 7 | `avatar_url`/`logo_url` = `null` sau `""` șterg imaginea (`exclude_unset`). Poza Google nu mai revine după ștergere. |
| 10 | Respingere definitivă (400/402/403/404/409/422) → tranzacția se închide + mesaj corect cu id-ul comenzii; erorile tranzitorii rămân „redeschide ecranul”. Backend: eșecurile furnizorului de plată sunt acum 503, nu 402. Cheie de idempotență stabilă la retry de transfer. |
| 11 | `AuthContext.refresh` nu mai face `setUser(null)` la eroare de rețea/5xx; apare ecran „Serverul nu răspunde” cu „Încearcă din nou”. |

## Performanță
* `bcrypt`, `argon2`, `smtplib` → `asyncio.to_thread`.
* `list_or_search` (shop și clothes): o singură interogare pentru „owned”.
* Tokenul din `?token=` e mascat în loguri (filtru pe `uvicorn.*`). **Nu acoperă logurile edge ale platformei**; soluția completă e un ticket WS de scurtă durată sau `Sec-WebSocket-Protocol`.

## De știut la deploy
* Colecție nouă `astran_money_journal` (index TTL 30 zile după finalizare) și câmpul `users._money_ops`; se creează la pornire.
* `/wallet/buy` poate întoarce acum 503 (reîncercabil) unde înainte dădea 402.
* Aplicația folosește IAP (Google/Apple), nu Stripe; Google/Apple nu sunt legate de cont (ar cere `obfuscatedAccountId` / `appAccountToken` în client).
* Nerevăzute: sandbox-ul Luau (C++), `chat_core`/`chat_filter` în detaliu, ecranele Studio/Shop.