# Audit politică de confidențialitate — Astran Game (2026-11-25)

> Testele trecute NU înseamnă conformitate GDPR. Politica 1.1 este o CIORNĂ „NECESITĂ COMPLETARE” și este blocată de la a fi prezentată ca finală.

## 1. Probleme identificate
1. Politica veche (frontend `privacyPolicy.*.ts`) promitea/permitea publicitate, personalizare, antrenare IA pe date (chat, voce, comportament), vânzare/licențiere de date către terți, chat vocal și cameră — **nimic din acestea nu există în cod**.
2. Marcaje necompletate (operator, dată, e-mail, website, adresă) afișabile utilizatorilor.
3. **Textul afișat în aplicație nu era cel din `privacyPolicy.ts`**: frontend-ul nu importă acel fișier; aplicația servește `backend/legal/privacy_policy/*`, dar `manifest.json` trimitea la `1.0.{ro,en}.txt` care **lipseau din arhivă** (serverul real nu pornește: fail-closed). Textul EN 1.0 din frontend avea exact SHA-256 din manifest; cel RO nu (nu poate fi reconstruit).
4. Doar `ro` și `en` sunt limbi suportate (`LangCode`); celelalte ~40 de fișiere `privacyPolicy.<lb>.ts` sunt cod mort, neîntreținut, cu aceleași afirmații false.
5. Politica nu spunea nimic despre prelucrările reale: vârstă/acord parental, WebAuthn, Google sign-in prin broker Emergent, IAP Google/Apple/Stripe, moderare DSA, ștergere cont, retenție.

## 2. Fișiere analizate
`backend/server.py`, `realtime.py`, `astran_sandbox/*` (privacy, privacy_export, consent, legal_docs, age_*, parental_*, chat_*, dm_*, moderation_*, project_security, money, refunds, trash_purge, requirements), `backend/legal/*`, `.env.example`, `frontend/package.json`, `app.json`, `src/i18n/privacyPolicy*.ts`, `LegalDocModal.tsx`. **Neanalizat în detaliu:** sandbox-ul Luau/C++, ecranele de UI individuale, CI/APK.

## 3. Fișiere modificate / adăugate
- NOU `backend/legal/privacy_policy/1.1.{ro,en}.txt` (sursa servită, cu SHA-256)
- `backend/legal/manifest.json` — intrarea `privacy_policy` 1.0 (fișiere inexistente) înlocuită cu 1.1, `status: draft_needs_completion`
- `backend/astran_sandbox/legal_docs.py` — detectare marcaje, `status`, `publication_blockers()`; `ASTRAN_REQUIRE_FINAL_LEGAL_DOCS=1` refuză pornirea cu documente draft (fără variabilă: doar `log.error`)
- `backend/astran_sandbox/consent_routes.py` — câmp `draft` în `/documents` (compatibil înapoi)
- NOU `backend/legal/data_inventory.json`, `sync_frontend_policy.py`, `astran_sandbox/test_privacy_policy.py`
- `frontend/src/i18n/privacyPolicy.{ro,en}.ts` — oglinzi GENERATE; `privacyPolicy.ts` comentat
- Nu am modificat autentificarea, plățile, chatul sau colectarea de date.

## 4. Practici confirmate în cod
Cont (email, username, parolă hash, limbă, sesiuni 7 zile), WebAuthn (cheie publică), Google sign-in prin `demobackend.emergentagent.com`; DOB criptat (Fernet), email părinte criptat, tokenuri HMAC, audit chain pseudonimizat; jocuri/avatar/modele; prezență în joc (TTL 60 s); chat de joc doar în memorie (stocat doar la raportare); chat de grup și DM stocate (DM sub 18: text brut nestocat); filtru de chat pe reguli (fără IA); prieteni/blocări/grupuri; rapoarte, decizii, contestații, contact; ledger, achiziții, rambursări; IAP Google Play/Apple, Stripe; SMTP; Redis opțional; IP în `security_audit_log` și rate limiting; export și ștergere cont GDPR; furnizori opționali de verificare vârstă/părinte (doar din env). **Nu există:** analytics, ads, SDK de crash, IA/ML, microfon, cameră, locație, push, vânzare de date.

## 5. Afirmații eliminate
Publicitate/marketing/personalizare ca scop de prelucrare; dezvoltarea/antrenarea IA pe chat, voce, comportament; vânzare/licențiere/retransmitere date către terți și persoane fizice; chat vocal și date audio; cameră/video; „procesare oriunde în lume”; „nu garantăm ștergerea”; transferul datelor deja partajate cu terți.

## 6. De completat de administrator (NU inventat)
1. Denumire legală, nr. înregistrare, sediu operator
2. E-mail de confidențialitate (+ website, DPO dacă există)
3. Data intrării în vigoare (și `effective_date` în manifest)
4. Furnizor de găzduire/baze de date (nume, țară) și Redis dacă e folosit
5. Furnizor SMTP
6. Furnizor(i) de verificare vârstă 18+ / parental (sau „niciunul”)
7. Garanțiile pentru transferuri în afara SEE (SCC/adecvare) pentru Google, Apple, Stripe, Emergent, hosting
8. Retenția backup-urilor
9. Confirmarea juridică a perioadelor: 5 ani financiar, 3 ani dovadă consimțământ, 365 zile moderare/securitate
10. Apoi: înlocuiește marcajele în ambele limbi, `status: "final"`, `effective_date`, `cd backend/legal && python rehash_manifest.py` (doar pentru versiune nepublicată), `python sync_frontend_policy.py`, setează `ASTRAN_REQUIRE_FINAL_LEGAL_DOCS=1` în producție.

## 7. Teste
`cd backend && python -m unittest astran_sandbox.test_privacy_policy -v` → **21 teste OK**: secțiuni obligatorii, aceeași structură RO/EN, marcaje identice și document blocat cât timp există, afirmații interzise, hash-uri manifest, oglinda frontend = textul servit, deriva codului (colecții Mongo, hosturi externe, dependențe backend/frontend, plugin-uri/permisiuni Expo). Verificat manual: o colecție nouă în cod face testul să eșueze.
Suita backend completă: 616 teste, 29 eșecuri/erori **identice înainte și după** (preexistente). Testele frontend și build-ul Expo nu au putut fi rulate aici.

## 8. Probleme GDPR rămase
- **`security_audit_log` (cu IP) nu are ștergere automată pentru conturi active** (doar după ștergerea contului) — decide o perioadă și implementeaz-o; politica spune deschis acest lucru.
- DM-urile și mesajele de grup nu au termen de păstrare.
- `ToS 1.0 ro`: hash neconcordant cu manifestul (preexistent) → serverul real nu pornește până nu e rezolvat; ToS 1.0/1.1 au și ele marcaje necompletate (poarta le blochează la fel).
- Dacă un utilizator a acceptat deja privacy 1.0 (hash din `consent_events`), recuperează textul original; eu am scos intrarea 1.0 din manifest fiindcă fișierele nu există.
- Consimțământul de marketing există, dar nu am găsit cod de trimitere marketing: nu activa trimiterea fără DPA/temei confirmat.
- Verificarea 18+ și părintească depinde de provideri neconfigurați; fără ei accesul 18+ rămâne blocat.
- Cele ~40 `privacyPolicy.<lb>.ts` neutilizate conțin încă afirmațiile false: recomand ștergerea lor (nu le-am șters fără acordul tău).
- Nu s-a făcut DPIA / evaluare juridică; politica trebuie revizuită de un jurist.
