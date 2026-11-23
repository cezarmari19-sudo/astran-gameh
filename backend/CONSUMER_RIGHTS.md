# Drepturile consumatorilor UE – implementare (Astran Game)

> Acest document descrie ce face codul. **Nu** este o declaratie de conformitate cu legislatia UE: vezi „Cerinte care necesita confirmare juridica”.

## Flux de achizitie
1. `POST /api/consumer/checkout` {product_type, product_id} → serverul intoarce produsul, pretul si moneda **din DB**, livrarea, taxele, comerciantul (din env),
   versiunile Termenilor / Politicii si textele EXACTE ale confirmarilor A si B (cu SHA-256).
2. `POST /api/consumer/checkout/{id}/confirm` {accept_immediate_performance: true, acknowledge_loss_of_withdrawal: true, presented_sha256} → doua randuri
   separate, adauga-doar, in `consumer_consents`. Booleene stricte; hash-urile trebuie sa corespunda textelor servite.
3. Achizitia (`/shop/items/{id}/buy`, `/clothes/items/{id}/buy`, `/wallet/stripe/intent`, `/wallet/buy`) re-verifica in DB checkout-ul, ambele confirmari,
   utilizatorul, produsul, pretul curent si ca checkout-ul nu a fost folosit. Fara ele: **428 `consumer_consent_required`**, fara debit/acces.
4. `consumer_orders` se scrie in acelasi jurnal atomic (`money.begin`) cu tranzactia din ledger; contine `consent_ids`, `consent_digest`, versiuni, comerciant.
5. Confirmare pe e-mail (best-effort, starea in `consumer_orders.email.status`).

**Limita Google Play / Apple:** plata se face in magazin inainte de apelul `/wallet/buy`; serverul nu o poate bloca. Daca lipsesc confirmarile valide, creditam
(consumatorul a platit) dar comanda are `withdrawal_right.status = "preserved"` (dreptul de retragere ramane). Blocarea reala exista in UI, la Stripe/mock si la Astrans.

## Endpoint-uri `/api/consumer/*`
Public: `GET /documents/{doc}`, `GET /trader`. Utilizator: `POST /checkout`, `GET /checkout/{id}`, `POST /checkout/{id}/confirm`, `GET /orders`, `GET /orders/{id}`,
`GET /refunds/transactions`, `POST /refunds`, `GET /refunds`, `GET /refunds/{id}`.
Admin (`is_platform_admin` citit din DB la fiecare cerere): `GET /admin/refunds`, `GET /admin/refunds/{id}`, `POST …/review`, `POST …/decision`, `POST …/execute`,
`POST …/confirm-external`, `GET /admin/audit`.

## Colectii noi
`consumer_checkouts`, `consumer_consents` (adauga-doar; unic (checkout_id, kind)), `consumer_orders` (unic tx_id, checkout_id), `refund_requests`
(`active_key` unic sparse = o singura cerere activa / tranzactie), `consumer_audit_log` (adauga-doar). Ledger-ul `astran_ledger` nu e modificat retroactiv.

## Rambursari
Statusuri: pending → under_review → approved | rejected → refunded (`processing_state`: idle/processing/awaiting_store_refund/failed_retryable/failed_definitive/done).
* Astrans cumparati cu bani: se retrag doar din soldul existent (debit atomic `$gte`, niciodata negativ); banii = proportional cu Astransii ramasi (rotunjit in jos, ≤ suma platita).
  Rambursare integrala doar prin `override` explicit al adminului (cu motiv, auditat). Stripe: apel real `POST /v1/refunds` cu `Idempotency-Key = refund_id`.
  Google/Apple: Astransii se retrag, iar rambursarea o face magazinul; adminul confirma cu referinta (`confirm-external`).
* Obiecte cumparate cu Astrans: cumparatorul primeste tot pretul; de la vanzator se retrage in limita soldului; restul il absoarbe platforma; accesul se revoca
  (`revoked: true`, randul ramane). Webhook-urile Stripe/Google pentru o rambursare initiata de platforma nu retrag a doua oara.
* Nerambursabile prin sistem: Astrans gratuiti/castigati/transferati, `purchase_reversal`, `group_create` (vezi probleme ramase).
* Retragerea (art. 9) dupa renuntare valida sau peste 14 zile nu se aproba fara override; neconformitatea (2019/770) ramane posibila.

## Configurare obligatorie
`CONSUMER_TRADER_*` (vezi `.env.example`). Fara ele, achizitiile cu plata sunt refuzate (503) – decizie deliberata (fail-closed).

## Teste
`python -m unittest astran_sandbox.test_zz_consumer_rights` (service + handler-e, FakeDB). Frontend: `node --experimental-strip-types --test src/i18n/consumerStrings.test.mts`.
