# astran-gameh

## Mesagerie privată între prieteni

Sistem **separat** de chatul din joc (nu folosește `chat_core.ChatDispatcher`, nu trece prin instanțele de joc).

- **Backend:** `backend/astran_sandbox/dm_core.py` (logică, permisiuni, filtrare, livrare), `dm_store.py` (MongoDB:
  `dm_conversations`, `dm_messages`), `dm_routes.py` (REST `/api/dm/*` + WebSocket `/ws/dm`).
- **Frontend:** tab-ul *Mesaje* (`app/(tabs)/messages.tsx`), conversația (`app/dm/[id].tsx`), starea în `src/dm/`.
  Din *Prieteni*, apăsarea pe un prieten deschide conversația persistentă cu el (`POST /api/dm/open`, idempotent).
- **Filtrare după vârstă:** aceeași sursă ca la chatul din joc (`chat_filter.shared_store(db)`, aceleași reguli și acțiuni
  `mask_word`/`mask_sentence`) și aceeași decizie `chat_core.text_for`: textul nefiltrat ajunge doar între doi utilizatori 18+;
  orice utilizator sub 18 primește numai varianta filtrată **de server**. Pentru expeditorii sub 18 nu se salvează textul brut.
- **Securitate:** expeditorul vine din sesiune; doar participanții accesează conversația (`404` pentru ceilalți); trimiterea cere
  prietenie + nicio blocare + utilizator nemutat; rate limiting pe utilizator; mesaj max. 500 de caractere (refuzat, nu tăiat).
- **Timp real:** WebSocket `/ws/dm` (server → client). Dacă Redis e configurat pentru multiplayer, e refolosit (canal `dm_<user_id>`).
- **Teste:** `cd backend && python -m unittest astran_sandbox.test_dm astran_sandbox.test_dm_routes -v` și
  `cd frontend && node --experimental-strip-types --test src/dm/dmStore.test.mts`.