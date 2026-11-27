# Astran Game — raport de remediere GDPR / DSA

> **Acest raport NU declară proiectul conform GDPR sau DSA.** Descrie ce s-a implementat tehnic și separat ce rămâne în sarcina oamenilor (juridic, operațional, furnizori externi).

## 1. Ce s-a verificat în cod înainte de implementare (constatări)
- **Recomandări (DSA Art. 27):** nu există profilare. „Pentru tine” și „În trend” sortează identic după `total_plays`, apoi `likes` (la fel pentru toți); „Populare” după `likes`, apoi `total_plays`; „Noi” după `created_at`. Singurul alt factor este filtrul de siguranță de vârstă (`age_category`). **Nu exista nicio opțiune de control** → s-a implementat una reală (vezi 3.3).
- **Shop (DSA Art. 30–32):** vânzarea este rezervată adulților verificați 18+; creatorii primesc Astrans, iar în cod **nu există retragere în bani reali**. Nu s-a presupus că un creator e comerciant: statutul este *declarat* și verificat.
- **Reautentificare:** exista deja `privacy.verify_reauth` (parolă sau cod pe e-mail) + `project_security` (rate limit, jurnal). **Este refolosit**, nu duplicat (inițial scrisesem unul propriu; l-am eliminat).
- **Data nașterii:** se seta o singură dată (`dob_enc`, criptat); nu exista corectare.

## 2. Fișiere
**Noi (backend):** `incidents.py`, `incident_routes.py`, `personal_data.py`, `dob_correction.py`, `personal_data_routes.py`, `merchants.py`, `merchant_routes.py`, `recommendations.py`, `compliance_migrations.py`, `test_gdpr_dsa_remediation.py`, `legal/terms_of_service/1.2.{ro,en}.txt`, `legal/privacy_policy/1.2.{ro,en}.txt`.
**Modificate (backend):** `server.py` (routere, migrări la pornire, Discover, `GET /games/recommendation-info`, `DELETE /games/recently-played`, indicii UI `is_legal_officer`/`is_security_officer`, căi permise pentru conturi restrânse/suspendate), `age_service.py` (prag de atestare din banda dorită + finalizarea corectării), `age_policy.py`, `moderation_service.py` (flag `security_officer`), `shop_routes.py`, `clothes_routes.py`, `consumer.py`, `user_settings_routes.py`, `privacy.py` (ștergere/export/retenție pentru colecțiile noi), `legal/manifest.json`, `legal/data_inventory.json`; teste adaptate: `test_shop_rules.py`, `test_zz_consumer_rights.py`, `test_moderation.py`, `test_privacy_policy.py`.
**Frontend noi:** `app/seller.tsx`, `app/confirm-email.tsx`, `app/revert-email.tsx`, `app/moderation/admin/incidents.tsx`, `app/moderation/admin/merchants.tsx`, `src/api/compliance.ts`, `src/i18n/complianceStrings.ts` (+ test), `src/components/{PersonalDataSection,RecommendationControls,SellerInfoCard}.tsx`.
**Frontend modificate:** `settings.tsx`, `shop.tsx`, `CheckoutSheet.tsx`, `moderation/admin/index.tsx`, `modUi.tsx` (Input securizat), `AuthContext.tsx`, `api/consumer.ts`, oglinzile `privacyPolicy.{ro,en}.ts`.

## 3. Funcționalități implementate

### 3.1 Incidente de securitate (GDPR Art. 33–34)
Model `security_incidents` + jurnal `security_incident_audit` **adaugă-doar, înlănțuit prin hash** (alterarea se detectează). Înregistrează: data descoperirii, tip, categorii de date, număr estimat de utilizatori (sau „necunoscut”), măsuri luate, gravitate, evaluarea riscului, decizia de notificare (cu motive și aprobare), termenul de 72 h (configurabil, stări: pending/due_soon/overdue/met/late/not_required), motivele întârzierii (obligatorii dacă depunerea e peste termen), decizia Art. 34 (cu excepțiile Art. 34(3)), proiecte de mesaj RO/EN pentru persoane, închidere cu „lecții învățate”.
Reguli: „nu se notifică” doar dacă riscul e „improbabil”; date de minori ⇒ gravitate minimă „high”; date din categorii speciale ≠ „improbabil”; închiderea cere deciziile Art. 33 și 34 finalizate; actualizare optimistă (`version`).
**Acces:** doar `is_platform_admin`, `is_legal_officer`, `is_security_officer` (verificare strictă `is True`, din DB, la fiecare cerere); alții primesc 403 generic. Aprobarea și înregistrarea depunerii: doar admin/responsabil juridic.
**Nu trimite nimic automat** (test static: fără SMTP/HTTP în modul): pregătește proiectul și *înregistrează* că responsabilul l-a depus pe canalul oficial.

### 3.2 Corectarea datelor (GDPR Art. 16) — Setări › Date personale
- **E-mail:** reautentificare (serviciul existent) → link cu token de 256 biți (stocat doar ca hash, single-use, 60 min) la **noua** adresă + notificare cu link de anulare/revenire pe adresa **veche** (valabil 7 zile și după confirmare = mecanismul de recuperare). `users.email` se schimbă doar după confirmare; sesiunile cad. Adresă deja folosită: răspuns identic (fără enumerare de conturi), nimic trimis, verificare repetată la confirmare + index unic. Audit cu adrese mascate.
- **Data nașterii:** reautentificare obligatorie; înregistrează data veche/nouă (criptate), momentul, motivul, metoda. Limite anti-ocolire configurabile (implicit: 1 corecție aplicată/cont, 30 zile între cereri, 3 cereri/an, o singură cerere deschisă). Direcția decide: *mai restrictiv* → aplicat imediat cu regulile de siguranță (cerere acord parental sub 16, pierderea accesului/atestării 18+); *aceeași bandă* → aplicat; *mai puțin restrictiv* (→16+, →18+) → **neaplicat** până la atestare de la furnizorul existent; fără furnizor conectat rămâne în așteptare și 18+ rămâne blocat. Nu se cer/stochează documente de identitate.
- Un cont restrâns (ex. data greșită ⇒ blocat sub 16) poate accesa aceste căi.

### 3.3 Recomandări (DSA Art. 27)
Sursa unică a parametrilor: `recommendations.py`. **ToS 1.2, secțiunea 10 (RO+EN)** documentează doar parametrii reali: `total_plays`, `likes`, `created_at`, `age_category` (filtru), `recent_play_categories` (doar opt-in). Importanța relativă = ordinea reală (fără ponderi numerice inventate). Control nou: **„Recomandări personalizate”**, **implicit OPRIT**, setare server-side `personalized_recommendations`; doar în „Pentru tine”: categoriile din ultimele 50 de jocuri „Jucate recent” (max. 3) trec înaintea celorlalte. Utilizatorul poate dezactiva oricând și poate șterge istoricul (`DELETE /games/recently-played`). Un test verifică automat că fiecare parametru din cod apare în ToS și că ToS nu inventează parametri.

### 3.4 Comercianți (DSA Art. 30–32 + informarea consumatorilor)
Profil `merchant_profiles`: statut `undeclared | non_trader | trader` (chestionar de autoevaluare; o declarație „nu sunt comerciant” contrazisă de răspunsuri e refuzată), date comerciant validate pe backend (nume, adresă, telefon, e-mail, registru/număr obligatoriu pentru societăți, TVA opțional, declarația de conformitate UE), verificare `unverified → in_review → verified | rejected` (admin/responsabil juridic; nu se verifică propriile date; respingerea cere motiv), reclasificare forțată în comerciant, semnale de revizuire (nr. vânzări/venit 12 luni, configurabile), audit.
**Restricționare:** publicarea/editarea/cumpărarea de articole **cu preț > 0 și publice** (Shop, Clothes, checkout) cere statut declarat și, pentru comercianți, date complete și verificare ≥ pragul din politică (implicit `in_review`; rejected nu vinde niciodată). Articolele gratuite nu sunt restricționate.
**Afișare:** pagina produsului și checkout-ul (înainte de finalizare, păstrat în instantaneul comenzii) arată statutul; doar pentru comercianți: nume, adresă, telefon, e-mail, registru, declarația UE, starea verificării. Non-comercianții: doar mențiunea că legislația UE de protecție a consumatorilor nu se aplică contractului cu ei; fără date personale. VAT și notele interne nu sunt publice.
Ștergere cont: non-comercianți șterși imediat; datele comercianților rămân pseudonimizate 183 zile (Art. 30(4), configurabil) apoi sunt șterse de `sweep_retention`.

### 3.5 Tehnic
Migrări: `compliance_migrations.py` (0001 incidente, 0002 corecții date personale, 0003 comercianți), registru separat `schema_migrations_compliance`, idempotente, `migrate_down` reversibil (datele nu se șterg implicit), rulează la pornire. Nu modifică documente existente. Backend validează toate câmpurile (și Pydantic `extra=forbid`); autentificare + autorizare + rate limiting pe toate rutele sensibile; fără chei/token-uri în răspunsuri/audit. Reguli configurabile (nu hardcodate): `incident_policy` și `merchant_policy` (colecția `app_config`, doar admin), variabile de mediu (mai jos). Sistemul de monetizare Astrans nu a fost modificat (singura atingere: poarta de vânzare + info vânzător în checkout).
Variabile de mediu noi: `ASTRAN_PUBLIC_APP_URL` (**obligatorie** pentru linkurile din e-mail), `ASTRAN_EMAIL_CHANGE_TTL_MINUTES`, `ASTRAN_EMAIL_CHANGE_REVERT_DAYS`, `ASTRAN_DOB_CORRECTION_{MAX_APPLIED,COOLDOWN_DAYS,MAX_PER_YEAR,PENDING_TTL_DAYS}`, `PRIVACY_MERCHANT_RETENTION_DAYS`.

## 4. Endpointuri
- **Incidente** (`/api/admin/incidents`, doar personal): `GET /me`, `GET /summary`, `GET|PUT /policy`, `GET ""`, `POST ""`, `GET|PATCH /{id}`, `POST /{id}/assessment`, `/authority/decision`, `/authority/approve`, `/authority/draft`, `/authority/submission`, `/data-subjects/decision`, `/data-subjects/communication`, `/close`, `GET /{id}/audit`.
- **Date personale:** `GET /api/account/personal-data`; `POST /api/account/email-change/{request|cancel|confirm|revert}`; `GET|POST /api/account/dob-correction`, `POST /api/account/dob-correction/cancel`. Codul de reautentificare pentru conturi Google: existentul `POST /api/privacy/reauth/code`.
- **Comercianți:** `GET|PUT /api/seller/status`; `GET /api/admin/merchants`, `GET|PUT /api/admin/merchants/policy`, `GET /api/admin/merchants/{user_id}`, `POST …/review`, `POST …/reclassify`.
- **Recomandări:** `GET /api/games/recommendation-info` (nou, public), `DELETE /api/games/recently-played` (nou), `GET /api/games/discover` (actualizat; răspunde cu `personalized`), setarea prin `/api/settings/me` (existent).
- **Actualizate:** liste/detaliu Shop & Clothes includ `seller`; checkout include `product.seller_info`; publish/update/buy întorc 409 cu `seller_status_required | trader_details_required | trader_verification_required | seller_not_compliant`.

## 5. Teste executate
| Suită | Rezultat |
|---|---|
| `test_gdpr_dsa_remediation` (nou) | **44/44 OK** — incidente (acces, termen 72 h, flux complet, tamper audit, aprobare, „nu trimite nimic”), e-mail (confirmare, revenire, adresă ocupată fără enumerare, expirare, rate limit, reauth refolosit), data nașterii (aplicare imediată/blocare 18+/atestare/limite/cooldown/validare), comercianți (statut, validare, verificare, restricție de vânzare, afișare publică, acces), recomandări (opt-in, ordine, ToS ⇄ cod), migrări, ștergere/export, căi pentru conturi restrânse |
| Backend complet | 793 teste; **fără regresii față de starea inițială** (aceleași 29 eșecuri/erori preexistente, toate din dependențe lipsă în sandbox: fastapi/pydantic/motor/bcrypt…) |
| Frontend `node --test` (`complianceStrings`, `consumerStrings`, `modStrings`) | 17/17 OK (paritate RO/EN, chei folosite în ecrane, coduri de eroare din backend acoperite, fără încărcare de documente) |
| `tsc` (doar sintaxă) pe fișierele noi/modificate | fără erori de sintaxă/tip proprii; erorile rămase sunt doar module lipsă (nu există `node_modules`) |

## 6. Ce NU s-a putut verifica (limite reale)
- **Rutele FastAPI nu au fost rulate** (fastapi/pydantic indisponibile, fără rețea): serviciile sunt testate pe codul real cu FakeDB; rutele sunt compilate și verificate static. **24 de teste existente** (`test_shop_rules`, `test_zz_consumer_rights`) care trec prin rute **nu pot rula aici** și le-am adaptat (vânzătorii de test declară „non_trader”) — trebuie rulate în CI.
- Frontend: nerulat pe dispozitiv/browser, fără type-check complet (necesită `npm install`).
- Nimic testat pe MongoDB real, cu SMTP real sau cu furnizor real de age verification.

## 7. Probleme care necesită furnizori externi sau intervenție juridică
1. **Furnizor de age verification:** fără el, corectările spre 16+/18+ rămân în așteptare (comportament dorit, dar funcția nu poate fi folosită).
2. **E-mail (SMTP) + `ASTRAN_PUBLIC_APP_URL` + deep links** `/confirm-email` și `/revert-email` configurate pe web/nativ.
3. **Revizuire juridică** a ToS 1.2 și Privacy Policy 1.2 (**ciorne**, cu marcaje `[DE CONFIRMAT]`/`[DE COMPLETAT]`; `effective_date` lipsă; am setat `requires_reacceptance: false` — informare, nu reacceptare: decizie juridică). Termenele de păstrare lipsesc.
4. **Cine este partea contractuală în Shop?** `consumer.py` prezintă Astran ca „comerciant”/vânzător pentru articole, iar modelul din cod este de piață cu creatori terți. De clarificat înainte ca textele de la 11.2 să fie finale.
5. Politica privind **copia documentului de identitate / contul de plată** (Art. 30(1)(b),(c)): implementarea NU colectează documente (cerință explicită) și nu există cont de plată (fără retragere în bani). Dacă legea sau un furnizor cere acestea, e nevoie de decizie + bază legală.
6. Calificarea efectivă a creatorilor ca „comercianți” (praguri/semnale configurabile, nu hotărâte de cod).

## 8. Obligații rămase neimplementate / operaționale
- Numirea DPO / punct de contact și **procedura internă** de incident (cine decide, cine depune la ANSPDCP, formare); depunerea se face manual pe portalul autorității.
- **Trimiterea în masă** a comunicărilor Art. 34 către utilizatori (există proiect RO/EN + înregistrare, nu mecanism de livrare; nu se păstrează liste de persoane afectate).
- **DSA Art. 32** (informarea cumpărătorilor despre produse ilegale), verificări periodice/suspendarea comercianților cu date inexacte (Art. 30(3)–(4) procedural), reclamații/mecanisme Art. 20 pentru comercianți — doar parțial acoperite de sistemul existent de moderare.
- Proces de suport pentru corectări peste limită (nu există UI de staff pentru DOB).
- **Impact operațional la lansare:** `enforce_seller_status` este **activ implicit** — vânzătorii existenți fără declarație nu mai pot vinde/fi cumpărați până declară statutul. Folosiți `PUT /api/admin/merchants/policy {"grace_until": "AAAA-LL-ZZ"}` dacă doriți o perioadă de grație.
- Conturile Google: după schimbarea e-mailului, autentificarea Google cu adresa veche poate crea cont nou (nu am analizat legarea Google; de verificat).
- Retenție pentru incidente/audit (nedefinită) și includerea colecțiilor staff în politica de păstrare.

## 9. Probleme preexistente găsite (nemodificate)
- `legal/manifest.json`: hash-ul ToS **1.0** nu corespunde fișierului din arhivă ⇒ `legal_docs.get_registry()` ridică `LegalDocsError` (versiunile publicate nu se editează; necesită corecție umană). Testele existente ocolesc problema cu un registru izolat.
- „Pentru tine” și „În trend” sunt identice în cod (documentat în ToS 10.2).
