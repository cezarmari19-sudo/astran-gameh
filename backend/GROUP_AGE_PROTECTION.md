# Protectie Adult <-> Minor in grupuri si chat

Regula (server-side, sursa de adevar: `astran_sandbox/contact_policy.py`): `under_18` si `adult_18` nu impart grupuri si
nu comunica prin chat. Categoria vine DOAR din `users.age_category` (DB). Lipsa/valoare invalida = **necunoscuta** =
refuz (nu se presupune adult sau minor).

| Zona | Mecanism |
|---|---|
| Grup nou | `groups.age_category` = categoria proprietarului (din DB), persistent; fara categorie cunoscuta nu se creeaza (inainte de debit) |
| Membri | `group_age.add_member` = singura cale (join, reintrare dupa leave, viitoare invitatii/coduri); re-verificare dupa scriere + compensare |
| Categoria grupului | nu exista ruta care o schimba; `UpdateGroupBody` respinge campuri necunoscute (422) |
| Chat de grup | trimitere: categoria expeditorului = a grupului; citire: doar mesaje din aceeasi categorie; nimic nu se salveaza/distribuie la refuz |
| Chat din joc (WebSocket) | `ChatDispatcher` livreaza doar destinatarilor compatibili (categoria citita din DB la fiecare mesaj); istoric la conectare filtrat; relay Redis filtrat la destinatar |
| `state.anim` (text liber) | livrat doar membrilor compatibili; ceilalti primesc pozitia fara `anim` (si in `roster` si prin Redis) |
| Grupuri mostenite | `python -m astran_sandbox.group_age_migration [--apply]`; mixte/necunoscute => inghetate (423), nimic sters; `--split GROUP_ID [--apply]` pentru separare |

Coduri de eroare: `contact_restricted` (403, generic), `age_category_unknown` (403), `group_age_review_required` (423).
Loguri: `astran.group_age` - evenimente cu pseudonime (hash), fara user_id/username/categorii.

Teste: `cd backend && python -m unittest astran_sandbox.test_group_age_protection astran_sandbox.test_chat astran_sandbox.test_chat_realtime astran_sandbox.test_contact_policy -v`
