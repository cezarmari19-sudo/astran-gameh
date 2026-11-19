# Documente juridice versionate (Terms of Service, Privacy Policy)

Sursa de adevar pentru „ce document a văzut utilizatorul” este serverul: `manifest.json` + fișierele
`<tip>/<versiune>.<limba>.txt`. La pornire, serverul recalculează SHA-256 al fiecărui fișier și REFUZĂ să pornească
dacă diferă de manifest (`legal_docs.py`). Amprenta textului prezentat se salvează în fiecare dovadă de acceptare
(`consent_events.document_sha256`).

## Reguli
- O versiune publicată NU se editează niciodată (nici măcar o virgulă) — se publică o versiune nouă.
- Fiecare versiune are texte `ro` și `en`.

## Cum publici o versiune nouă
1. Adaugă `privacy_policy/1.1.ro.txt` și `privacy_policy/1.1.en.txt` (sau `terms_of_service/...`).
2. În `manifest.json`, la documentul respectiv: adaugă o intrare în `versions` (cu `sha256` pentru fiecare fișier:
   `sha256sum <fisier>`), setează `current` la noua versiune și decide:
   - `"requires_reacceptance": true`  -> modificare materială: utilizatorii existenți vor fi puși să accepte din nou;
   - `"requires_reacceptance": false` -> doar informare: apare în Setări › Privacy ca „Actualizat (doar informare)”.
   Completează `summary.ro/en` (o propoziție despre ce s-a schimbat).
3. Rulează `python -m unittest astran_sandbox.test_consent`.
4. Versiunile vechi rămân în manifest și pe disc (dovada a ceea ce a fost prezentat).

## Consimțăminte opționale (marketing etc.)
Se definesc în `astran_sandbox/consent.py` (`PURPOSES`) și doar pentru prelucrări care chiar există în aplicație.
Dacă schimbi textul unui consimțământ, crește `version`-ul lui. Orice cod care prelucrează pe baza unui consimțământ
trebuie să folosească `consent.require_consent(...)` / `consent.purpose_audience(...)`.