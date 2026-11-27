"""Teste drepturile consumatorilor + rambursari. Ruleaza:  python -m unittest astran_sandbox.test_zz_consumer_rights -v

Folosesc FakeDB (fara MongoDB) si apeleaza DIRECT handler-ele rutelor (stub fastapi din test_support_fakeweb daca fastapi lipseste).
NU acopera: validarea pydantic reala (extra=forbid / StrictBool), autentificarea JWT din server.py, apelurile reale Stripe/Google/Apple
si cursele reale pe MongoDB. Vezi CONSUMER_RIGHTS.md.
"""
import asyncio
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

from astran_sandbox import test_support_fakeweb
test_support_fakeweb.install()
from astran_sandbox import test_support_fakedb as F  # noqa: E402

try:
    import pymongo.errors as _pe  # noqa: E402
except Exception:  # noqa: BLE001
    pm, _pe = types.ModuleType("pymongo"), types.ModuleType("pymongo.errors")
    _pe.DuplicateKeyError = F.DuplicateKeyError
    pm.errors = _pe
    pm.ReturnDocument = types.SimpleNamespace(BEFORE=False, AFTER=True)
    sys.modules["pymongo"], sys.modules["pymongo.errors"] = pm, _pe
F.DuplicateKeyError = _pe.DuplicateKeyError   # aceeasi clasa peste tot (money.py / refunds.py prind DuplicateKeyError)

from astran_sandbox import consumer as C, refunds as R, money, payment_reversal as PR  # noqa: E402
from astran_sandbox import consumer_routes, shop_routes  # noqa: E402

NS = types.SimpleNamespace
NOW = datetime.now(timezone.utc)
os.environ.update({"CONSUMER_TRADER_NAME": "Test SRL", "CONSUMER_TRADER_ADDRESS": "Str. Test 1, Bucuresti", "CONSUMER_TRADER_EMAIL": "contact@test.ro",
                   "STRIPE_SECRET_KEY": "sk_test_x", "ALLOW_MOCK_PAYMENTS": "1"})


def run(coro):
    return asyncio.run(coro)


def U(uid, bal=0, admin=False):
    return {"user_id": uid, "username": uid, "email": f"{uid}@e.com", "astrans_balance": bal, "is_platform_admin": admin, "language": "ro"}


class FakeStripe:
    """Inlocuieste HTTP-ul Stripe: inregistreaza apelurile si respecta cheia de idempotenta."""
    def __init__(self):
        self.calls, self.seen, self.mode = [], {}, "ok"

    async def post(self, url, data, headers, key):
        self.calls.append((url, dict(data), dict(headers)))
        idem = headers["Idempotency-Key"]
        if self.mode == "down":
            return 503, {}
        if self.mode == "reject":
            return 400, {"error": {"code": "charge_already_refunded"}}
        self.seen.setdefault(idem, {"id": "re_" + idem, "status": "succeeded"})
        return 200, self.seen[idem]


def _terms_only_registry():
    """In arhiva primita lipseste legal/privacy_policy/* si terms_of_service 1.0 nu mai corespunde hash-ului din manifest, deci registrul real nu porneste.
    Pentru teste: registru temporar cu terms_of_service 1.1 (real, copiat), suficient pentru versiunea citita de consumer.py."""
    import json, pathlib, shutil, tempfile
    from astran_sandbox import legal_docs as LD
    src = pathlib.Path(LD.DEFAULT_LEGAL_DIR)
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "terms_of_service").mkdir()
    for lang in ("ro", "en"):
        shutil.copy(src / "terms_of_service" / f"1.1.{lang}.txt", tmp / "terms_of_service" / f"1.1.{lang}.txt")
    m = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    tos = m["documents"]["terms_of_service"]
    tos["versions"] = [v for v in tos["versions"] if v["version"] == "1.1"]   # v1.0 din arhiva are hash diferit de manifest (problema preexistenta)
    m["documents"] = {"terms_of_service": tos}
    (tmp / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    LD.reset_registry_for_tests(LD.LegalRegistry(tmp))


class Base(unittest.TestCase):
    def setUp(self):
        _terms_only_registry()
        from astran_sandbox.ratelimit import SlidingWindowLimiter
        for n in ("_write", "_read", "_admin"):   # limitatoarele sunt la nivel de modul: le resetam per test
            setattr(consumer_routes, n, SlidingWindowLimiter(10 ** 6, 3600))
        C.reset_for_tests()
        self.db = F.FakeDB()
        self.stripe = FakeStripe()
        self.procs = {"stripe": R.StripeProcessor(http_post=self.stripe.post)}
        db = self.db

        async def getu(uid=None):
            return None
        self.r = {}
        cr = consumer_routes.make_consumer_router(getu, db, processors=self.procs)
        self.r.update(cr.routes)
        shop = shop_routes.make_shop_router(getu, db)
        self.r.update(shop.routes)
        self.buyer, self.other, self.seller, self.admin = U("buyer", 1000), U("other", 500), U("seller", 0), U("admin", 0, True)
        run(self._seed())

    async def _seed(self):
        db = self.db
        await C.ensure_indexes(db); await R.ensure_indexes(db); await money.ensure_indexes(db)
        for u in (self.buyer, self.other, self.seller, self.admin):
            await db.users.insert_one(dict(u))
        await db.platform_config.insert_one({"key": "astran_packages", "currency": "RON", "packages": [
            {"package_id": "plus_300", "astrans": 300, "price": 11.99, "label": "Plus"}]})
        await db.merchant_profiles.insert_one({"user_id": "seller", "seller_status": "non_trader"})   # DSA Art. 30: statut declarat (merchants.py)
        await db.shop_items.insert_one({"item_id": "i1", "kind": "model", "owner_id": "seller", "owner_username": "seller", "name": "Copac", "description": "",
                                        "price": 100, "is_public": True, "downloads": 0, "created_at": NOW, "model": {"parts": []}})

    def call(self, method, path, *a, **k):
        return self.r[(method, path)](*a, **k)

    def code(self, coro):
        """(status, code) al unei HTTPException sau ConsumerError; orice alta exceptie e re-ridicata (nu o mascam)."""
        try:
            run(coro)
        except Exception as e:  # noqa: BLE001
            st = getattr(e, "status_code", None) or getattr(e, "status", None)
            if not isinstance(st, int):
                raise
            d = e.detail() if callable(getattr(e, "detail", None)) else getattr(e, "detail", None)
            return st, (d.get("code") if isinstance(d, dict) else d)
        return None, None

    # ---- ajutoare de flux
    async def checkout(self, user, ptype="shop_item", pid="i1"):
        return await self.call("POST", "/consumer/checkout", NS(product_type=ptype, product_id=pid, language="ro"), user)

    async def consent(self, user, co, a=True, b=True):
        sh = {k: v["sha256"] for k, v in co["consent_texts"].items()}
        return await self.call("POST", "/consumer/checkout/{checkout_id}/confirm", co["checkout_id"],
                               NS(accept_immediate_performance=a, acknowledge_loss_of_withdrawal=b, presented_sha256=sh), user)

    async def buy_item(self, user, checkout_id):
        u = await self.db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
        return await self.call("POST", "/shop/items/{item_id}/buy", "i1", u, NS(checkout_id=checkout_id))

    async def bought_item(self):
        co = await self.checkout(self.buyer)
        await self.consent(self.buyer, co)
        return co, await self.buy_item(self.buyer, co["checkout_id"])

    async def bal(self, uid):
        return (await self.db.users.find_one({"user_id": uid}))["astrans_balance"]

    async def seed_package_purchase(self, user="buyer", ref="pi_abc", waiver=True, astrans=300, price=11.99, provider="stripe"):
        """Oglindeste /wallet/buy din server.py (checkout -> require_checkout -> build_order + ledger)."""
        db = self.db
        u = await db.users.find_one({"user_id": user}, {"_id": 0})
        ctx = None
        if waiver:
            co = await self.checkout(u, "astran_package", "plus_300")
            await self.consent(u, co)
            ctx = await C.require_checkout(db, u, "astran_package", "plus_300", co["checkout_id"], hard=True)
        else:
            ctx = await C.require_checkout(db, u, "astran_package", "plus_300", None, hard=False)
        tx = {"tx_id": "tx_" + ref, "user_id": user, "counterparty_id": "system", "type": "purchase", "amount": astrans, "fee": 0, "net": astrans,
              "status": "completed", "timestamp": NOW, "reference": "buy:plus_300:" + provider, "provider": provider, "price": price,
              "currency": "RON", "package_id": "plus_300", "provider_ref": ref}
        order, upd = C.build_order(ctx, u, tx["tx_id"], provider)
        tx["order_id"] = order["order_id"]
        await db.astran_ledger.insert_one(tx); await db.consumer_orders.insert_one(order)
        if upd:
            await db.consumer_checkouts.update_one(upd["filter"], upd["update"])
        return tx, order

    async def request_refund(self, user, tx_id, reason="defective", explanation=None):
        return await self.call("POST", "/consumer/refunds", NS(tx_id=tx_id, reason=reason, explanation=explanation), user)

    async def decide(self, rid, decision="approve", reasoning="ok decision", override=False, admin=None):
        return await self.call("POST", "/consumer/admin/refunds/{refund_id}/decision", rid,
                               NS(decision=decision, reasoning=reasoning, override=override), admin or self.admin)

    async def execute(self, rid, admin=None):
        return await self.call("POST", "/consumer/admin/refunds/{refund_id}/execute", rid, admin or self.admin)


class PurchaseConsentTests(Base):
    def test_1_purchase_without_confirmations_is_blocked(self):
        async def go():
            self.assertEqual(await self.bal("buyer"), 1000)
            co = await self.checkout(self.buyer)
            self.assertTrue(co["needs_consent"])
            # fara confirmari: nici macar checkout-ul "open" nu permite cumpararea
            return co
        co = run(go())
        self.assertEqual(self.code(self.buy_item(self.buyer, co["checkout_id"])), (428, "consumer_consent_required"))
        self.assertEqual(self.code(self.buy_item(self.buyer, None)), (428, "consumer_consent_required"))
        self.assertEqual(run(self.bal("buyer")), 1000)                      # fara debit
        self.assertEqual(len(self.db.shop_purchases.docs), 0)               # fara acces acordat
        self.assertEqual(len(self.db.consumer_orders.docs), 0)

    def test_2_purchase_with_valid_confirmations_is_recorded(self):
        async def go():
            co, res = await self.bought_item()
            return co, res
        co, res = run(go())
        self.assertTrue(res["ok"]); self.assertEqual(res["order"]["withdrawal_right"]["status"], "waived")
        cons = self.db.consumer_consents.docs
        self.assertEqual(sorted(c["kind"] for c in cons), sorted([C.KIND_A, C.KIND_B]))   # doua inregistrari separate
        for c in cons:
            self.assertEqual(c["user_id"], "buyer"); self.assertEqual(c["checkout_id"], co["checkout_id"]); self.assertIs(c["accepted"], True)
            self.assertIsInstance(c["created_at"], datetime); self.assertTrue(c["text"]); self.assertEqual(len(c["text_sha256"]), 64)
            self.assertEqual(c["texts_version"], C.TEXTS_VERSION); self.assertEqual(c["terms_version"], "1.1"); self.assertEqual(c["price_amount"], 100)
            if c["kind"] == C.KIND_A:
                self.assertIn("Copac", c["text"])
        order = self.db.consumer_orders.docs[0]
        tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
        self.assertEqual(order["tx_id"], tx["tx_id"]); self.assertEqual(tx["order_id"], order["order_id"]); self.assertEqual(order["user_id"], "buyer")
        self.assertEqual(sorted(order["withdrawal_right"]["consent_ids"]), sorted(c["consent_id"] for c in cons))
        self.assertTrue(order["consent_digest"])
        self.assertEqual(run(self.bal("buyer")), 900); self.assertEqual(run(self.bal("seller")), 95)   # pret + comision 5%
        # checkout-ul nu poate fi refolosit pentru o a doua comanda
        u = self.buyer
        self.assertEqual(self.code(C.require_checkout(self.db, u, "shop_item", "i1", co["checkout_id"], hard=True)), (428, "consumer_consent_required"))
        self.assertEqual(len(self.db.consumer_orders.docs), 1)

    def test_3_forged_confirmations_are_rejected(self):
        async def go():
            co = await self.checkout(self.buyer)
            return co
        co = run(go())
        cid, sh = co["checkout_id"], {k: v["sha256"] for k, v in co["consent_texts"].items()}
        conf = lambda user, a, b, s=sh, c=cid: self.call("POST", "/consumer/checkout/{checkout_id}/confirm", c,  # noqa: E731
                                                        NS(accept_immediate_performance=a, acknowledge_loss_of_withdrawal=b, presented_sha256=s), user)
        self.assertEqual(self.code(conf(self.buyer, True, False))[1], "confirmations_required")      # doar A
        self.assertEqual(self.code(conf(self.buyer, False, True))[1], "confirmations_required")      # doar B
        self.assertEqual(self.code(conf(self.buyer, "true", "true"))[1], "confirmations_required")   # nu e booleanul True
        self.assertEqual(self.code(conf(self.buyer, 1, 1))[1], "confirmations_required")
        self.assertEqual(self.code(conf(self.buyer, True, True, s={"A": "x", "B": "y"}))[1], "texts_changed")   # text nevazut/alterat
        self.assertEqual(self.code(conf(self.other, True, True))[0], 404)                             # checkout-ul altuia
        self.assertEqual(len(self.db.consumer_consents.docs), 0)
        # cod modificat care ar seta direct statusul "consented" fara dovezi: serverul re-verifica in DB
        run(self.db.consumer_checkouts.update_one({"checkout_id": cid}, {"$set": {"status": "consented"}}))
        self.assertEqual(self.code(self.buy_item(self.buyer, cid)), (428, "consumer_consent_required"))
        # un alt utilizator nu poate folosi checkout-ul meu
        self.assertEqual(self.code(self.buy_item(self.other, cid)), (428, "consumer_consent_required"))
        # confirmari valide, apoi pretul urca: achizitia se opreste (pret manipulat / schimbat)
        run(self.db.consumer_checkouts.update_one({"checkout_id": cid}, {"$set": {"status": "open"}}))
        run(self.consent(self.buyer, co))
        run(self.db.shop_items.update_one({"item_id": "i1"}, {"$set": {"price": 999}}))
        self.assertEqual(self.code(self.buy_item(self.buyer, cid)), (409, "price_changed"))
        self.assertEqual(run(self.bal("buyer")), 1000)

    def test_confirm_is_idempotent_and_expiry(self):
        async def go():
            co = await self.checkout(self.buyer)
            await self.consent(self.buyer, co); await self.consent(self.buyer, co)
            self.assertEqual(len(self.db.consumer_consents.docs), 2)
            co2 = await self.checkout(self.buyer)
            await self.db.consumer_checkouts.update_one({"checkout_id": co2["checkout_id"]}, {"$set": {"expires_at": NOW - timedelta(minutes=1)}})
            return co2
        co2 = run(go())
        self.assertEqual(self.code(self.consent(self.buyer, co2))[0], 410)

    def test_own_item_free_item_and_missing_trader(self):
        async def go():
            await self.db.shop_items.update_one({"item_id": "i1"}, {"$set": {"owner_id": "buyer"}})
        run(go())
        self.assertEqual(self.code(self.checkout(self.buyer))[1], "own_product")
        run(self.db.shop_items.update_one({"item_id": "i1"}, {"$set": {"owner_id": "seller", "price": 0}}))
        self.assertEqual(self.code(self.checkout(self.buyer))[1], "free_product")
        saved = os.environ.pop("CONSUMER_TRADER_NAME")
        try:
            self.assertEqual(self.code(self.checkout(self.buyer, "astran_package", "plus_300")), (503, "trader_info_missing"))
        finally:
            os.environ["CONSUMER_TRADER_NAME"] = saved

    def test_not_every_digital_purchase_loses_withdrawal_right(self):
        async def go():
            C.CATEGORIES["astran_package"]["delivery"] = "deferred"
            try:
                co = await self.checkout(self.buyer, "astran_package", "plus_300")
                self.assertFalse(co["needs_consent"])       # livrare ulterioara: fara renuntare
                self.assertIsNone(co["consent_texts"])
            finally:
                C.CATEGORIES["astran_package"]["delivery"] = "immediate"
            # plata din magazin FARA confirmari (soft): se livreaza, dar dreptul de retragere se pastreaza
            tx, order = await self.seed_package_purchase(waiver=False, ref="pi_nowaiver")
            self.assertEqual(order["withdrawal_right"]["status"], "preserved")
            self.assertEqual(order["withdrawal_right"]["basis"], "no_valid_confirmations")
            # tranzactie veche, fara comanda: se pastreaza in termen de 14 zile
            ws = C.withdrawal_status(None, NOW - timedelta(days=3))
            self.assertTrue(ws["withdrawal_right_applies"])
            self.assertFalse(C.withdrawal_status(None, NOW - timedelta(days=20))["withdrawal_right_applies"])
        run(go())

    def test_confirmation_email_and_policy_docs(self):
        sent = []

        async def notifier(to, subj, body):
            sent.append((to, subj, body)); return True
        C.configure(notifier=notifier)

        async def go():
            co, res = await self.bought_item()
            order = await self.db.consumer_orders.find_one({})
            st = await C.send_confirmation(self.db, order, self.buyer)
            return st, order
        st, order = run(go())
        self.assertEqual(st, "sent"); self.assertEqual(self.db.consumer_orders.docs[0]["email"]["status"], "sent")
        self.assertIn(order["order_id"], sent[0][2]); self.assertIn("Test SRL", sent[0][2]); self.assertIn("art. 16 lit. (m)", sent[0][2])
        # politica: accesibila public, ro/en, fallback pe en pentru alte limbi, amprenta SHA-256
        for lang, expect in (("ro", "POLITICA DE RAMBURSARE"), ("en", "REFUND POLICY"), ("de", "REFUND POLICY"), (None, "REFUND POLICY")):
            d = run(self.call("GET", "/consumer/documents/{doc_type}", "refund_policy", None, lang))
            self.assertIn(expect, d["text"]); self.assertEqual(len(d["sha256"]), 64)
        self.assertEqual(self.code(self.call("GET", "/consumer/documents/{doc_type}", "nope", None, None))[0], 404)


class RefundTests(Base):
    def test_4_5_own_vs_foreign_transaction(self):
        async def go():
            await self.bought_item()
            tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
            return tx
        tx = run(go())
        self.assertEqual(self.code(self.request_refund(self.other, tx["tx_id"]))[0], 404)           # tranzactia altuia
        self.assertEqual(self.code(self.request_refund(self.seller, tx["tx_id"]))[0], 404)          # nici vanzatorul
        req = run(self.request_refund(self.buyer, tx["tx_id"], "defective", "nu merge"))
        self.assertEqual(req["status"], "pending")
        mine = run(self.call("GET", "/consumer/refunds", self.buyer))["requests"]
        self.assertEqual([m["refund_id"] for m in mine], [req["refund_id"]])
        self.assertEqual(run(self.call("GET", "/consumer/refunds", self.other))["requests"], [])
        self.assertEqual(self.code(self.call("GET", "/consumer/refunds/{refund_id}", req["refund_id"], self.other))[0], 404)
        self.assertEqual(self.code(self.request_refund(self.buyer, tx["tx_id"], "bogus"))[1], "invalid_reason")

    def test_non_refundable_types_by_astran_source(self):
        async def go():
            db = self.db
            for t, extra in (("welcome_bonus", {"counterparty_id": "system"}), ("transfer_in", {"counterparty_id": "other"}),
                             ("transfer_out", {"counterparty_id": "other"}), ("shop_sale", {"counterparty_id": "other"}),
                             ("purchase_reversal", {"counterparty_id": "system"}), ("group_create", {"counterparty_id": "system"})):
                await db.astran_ledger.insert_one({"tx_id": "tx_" + t, "user_id": "buyer", "type": t, "amount": 10, "status": "completed", "timestamp": NOW, **extra})
        run(go())
        cats = {}
        for t in ("welcome_bonus", "transfer_in", "transfer_out", "shop_sale", "purchase_reversal", "group_create"):
            status, code = self.code(self.request_refund(self.buyer, "tx_" + t))
            self.assertEqual((status, code), (422, "transaction_not_refundable"), t)
        self.assertEqual(R.classify_tx({"type": "welcome_bonus", "counterparty_id": "system"})["category"], "free_astrans")
        self.assertEqual(R.classify_tx({"type": "shop_sale"})["category"], "earned_astrans")
        self.assertEqual(R.classify_tx({"type": "transfer_out"})["category"], "transferred_astrans")
        self.assertEqual(R.classify_tx({"type": "shop_purchase", "status": "completed"})["category"], "spent_astrans")
        self.assertEqual(R.classify_tx({"type": "purchase", "status": "completed"})["category"], "bought_astrans")

    def test_6_duplicate_requests_and_double_refund(self):
        async def go():
            await self.bought_item()
            tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
            a, b = await asyncio.gather(self.request_refund(self.buyer, tx["tx_id"]), self.request_refund(self.buyer, tx["tx_id"]), return_exceptions=True)
            return tx, a, b
        tx, a, b = run(go())
        self.assertEqual(sum(1 for x in (a, b) if isinstance(x, dict)), 1)                           # o singura cerere activa
        self.assertEqual(len(self.db.refund_requests.docs), 1)
        rid = self.db.refund_requests.docs[0]["refund_id"]
        self.assertEqual(self.code(self.request_refund(self.buyer, tx["tx_id"]))[1], "duplicate_request")
        run(self.decide(rid)); run(self.execute(rid))
        self.assertEqual(self.code(self.execute(rid))[0], 409)                                         # executie dubla
        self.assertEqual(self.code(self.decide(rid))[0], 409)                                          # aprobare dupa rambursare
        self.assertEqual(self.code(self.request_refund(self.buyer, tx["tx_id"]))[1], "already_refunded")
        self.assertEqual(run(self.bal("buyer")), 1000)                                                 # 900 + 100, o singura data

    def test_parallel_execute_refunds_once(self):
        async def go():
            await self.bought_item()
            tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
            rid = (await self.request_refund(self.buyer, tx["tx_id"]))["refund_id"]
            await self.decide(rid)
            res = await asyncio.gather(self.execute(rid), self.execute(rid), return_exceptions=True)
            return res
        res = run(go())
        self.assertEqual(sum(1 for x in res if isinstance(x, dict)), 1)
        self.assertEqual(run(self.bal("buyer")), 1000)
        self.assertEqual(len([t for t in self.db.astran_ledger.docs if t["type"] == "refund_in"]), 1)

    def test_7_used_item_refund_rules_and_ledger_immutability(self):
        async def go():
            await self.bought_item()
            u = await self.db.users.find_one({"user_id": "buyer"}, {"_id": 0})
            d = await self.call("GET", "/shop/items/{item_id}", "i1", u)       # buyer a primit continutul = furnizare inceputa
            self.assertIn("parts", d["item"])
            tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
            return tx
        tx = run(go())
        before = dict(tx)
        self.assertTrue(self.db.shop_purchases.docs[0].get("content_first_accessed_at"))
        # retragere dupa renuntare valida + furnizare: NU se aproba fara override explicit
        w = run(self.request_refund(self.buyer, tx["tx_id"], "withdrawal"))
        self.assertFalse(self.db.refund_requests.docs[0]["eligibility"]["withdrawal_right_applies"])
        self.assertEqual(self.code(self.decide(w["refund_id"]))[1], "withdrawal_not_applicable")
        det = run(self.call("GET", "/consumer/admin/refunds/{refund_id}", w["refund_id"], self.admin))
        self.assertTrue(det["usage"]["content_first_accessed_at"]); self.assertEqual(len(det["consents"]), 2)   # adminul vede confirmarile
        run(self.decide(w["refund_id"], "reject", "Dreptul de retragere a fost pierdut conform art. 16(m); poti cere pe motiv de neconformitate."))
        self.assertEqual(self.db.refund_requests.docs[0]["status"], "rejected")
        # neconformitate: drepturile raman chiar daca obiectul a fost folosit -> se poate aproba
        d = run(self.request_refund(self.buyer, tx["tx_id"], "defective", "scriptul nu porneste"))
        run(self.decide(d["refund_id"])); out = run(self.execute(d["refund_id"]))
        self.assertEqual(out["status"], "refunded")
        self.assertEqual(run(self.bal("buyer")), 1000); self.assertEqual(run(self.bal("seller")), 0)   # vanzatorul a avut 95, i s-au retras
        self.assertEqual(self.db.shop_purchases.docs[0]["revoked"], True)                              # acces retras, randul pastrat
        u = run(self.db.users.find_one({"user_id": "buyer"}, {"_id": 0}))
        d2 = run(self.call("GET", "/shop/items/{item_id}", "i1", {**u, "is_platform_admin": False}))
        self.assertFalse(d2["item"]["owned"]); self.assertNotIn("parts", d2["item"])                 # fara continut dupa rambursare
        orig = [t for t in self.db.astran_ledger.docs if t["tx_id"] == tx["tx_id"]][0]
        self.assertEqual(orig, before)                                                                 # tranzactia initiala NU a fost modificata
        self.assertEqual(sorted(t["type"] for t in self.db.astran_ledger.docs if t["type"].startswith("refund")), ["refund_in", "refund_out"])
        self.assertEqual(self.code(self.buy_item(self.buyer, None))[1], "purchase_refunded")           # nu se recumpara pe tacute

    def test_item_refund_seller_without_funds_never_goes_negative(self):
        async def go():
            await self.bought_item()
            await self.db.users.update_one({"user_id": "seller"}, {"$set": {"astrans_balance": 20}})   # vanzatorul a cheltuit din incasari
            tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
            rid = (await self.request_refund(self.buyer, tx["tx_id"]))["refund_id"]
            await self.decide(rid); await self.execute(rid)
            return rid
        rid = run(go())
        self.assertEqual(run(self.bal("seller")), 0); self.assertEqual(run(self.bal("buyer")), 1000)    # consumatorul primeste tot
        p = self.db.refund_requests.docs[0]["plan"]
        self.assertEqual((p["seller_reclaim"], p["platform_absorbed"]), (20, 80))                       # restul il suporta platforma

    def test_8_9_astrans_purchase_refund_via_processor(self):
        async def go():
            tx, order = await self.seed_package_purchase()
            # 120 din cei 300 Astrans cumparati au fost cheltuiti: raman 180 in portofel
            await self.db.users.update_one({"user_id": "buyer"}, {"$set": {"astrans_balance": 180}})
            req = await self.request_refund(self.buyer, tx["tx_id"], "defective")
            ap = await self.decide(req["refund_id"], reasoning="neconform confirmat")
            return tx, req, ap
        tx, req, ap = run(go())
        p = self.db.refund_requests.docs[0]["plan"]
        self.assertEqual(p["astrans_to_reclaim"], 180); self.assertEqual(p["spent_astrans_unrecovered"], 120)
        self.assertEqual(p["money_refund_minor"], 1199 * 180 // 300)                                    # proportional, in jos
        self.assertLessEqual(p["money_refund_minor"], 1199)
        out = run(self.execute(req["refund_id"]))
        self.assertEqual(out["status"], "refunded")
        url, data, hdr = self.stripe.calls[0]
        self.assertEqual(url, "https://api.stripe.com/v1/refunds"); self.assertEqual(data["payment_intent"], "pi_abc")
        self.assertEqual(data["amount"], str(p["money_refund_minor"])); self.assertEqual(hdr["Idempotency-Key"], req["refund_id"])
        self.assertEqual(run(self.bal("buyer")), 0)                                                    # niciodata negativ
        row = [t for t in self.db.astran_ledger.docs if t["type"] == "purchase_refund"][0]
        self.assertEqual(row["original_tx_id"], tx["tx_id"])
        self.assertEqual([t for t in self.db.astran_ledger.docs if t["tx_id"] == tx["tx_id"]][0]["status"], "completed")   # originalul intact
        self.assertEqual(self.code(self.execute(req["refund_id"]))[0], 409)
        self.assertEqual(len(self.stripe.calls), 1)                                                    # o singura rambursare la procesator
        # webhook-ul Stripe pentru aceeasi rambursare NU mai retrage Astrans a doua oara
        self.assertEqual(run(PR.reverse_purchase(self.db, "pi_abc", "stripe_refund")), "already")
        self.assertTrue(run(PR.platform_initiated_for_ref(self.db, "pi_abc")))
        self.assertEqual(run(self.bal("buyer")), 0)

    def test_all_spent_needs_override_and_still_not_negative(self):
        async def go():
            tx, _ = await self.seed_package_purchase()
            await self.db.users.update_one({"user_id": "buyer"}, {"$set": {"astrans_balance": 0}})
            req = await self.request_refund(self.buyer, tx["tx_id"], "defective")
            return req
        req = run(go())
        self.assertEqual(self.code(self.decide(req["refund_id"]))[1], "nothing_refundable")
        run(self.decide(req["refund_id"], override=True, reasoning="rambursare integrala impusa de lege"))
        p = self.db.refund_requests.docs[0]["plan"]
        self.assertEqual((p["money_refund_minor"], p["astrans_to_reclaim"]), (1199, 0))
        run(self.execute(req["refund_id"]))
        self.assertEqual(run(self.bal("buyer")), 0)

    def test_processor_transient_retry_is_safe_and_definitive_failure_compensates(self):
        async def setup(ref):
            tx, _ = await self.seed_package_purchase(ref=ref)
            await self.db.users.update_one({"user_id": "buyer"}, {"$set": {"astrans_balance": 300}})
            rid = (await self.request_refund(self.buyer, tx["tx_id"], "defective"))["refund_id"]
            await self.decide(rid)
            return rid
        rid = run(setup("pi_t1"))
        self.stripe.mode = "down"
        self.assertEqual(self.code(self.execute(rid)), (503, "provider_unavailable"))
        self.assertEqual(run(self.bal("buyer")), 0)                                                    # Astransii raman retrasi (nu se pot cheltui)
        self.stripe.mode = "ok"
        run(self.execute(rid))                                                                         # reincercare: fara dubla retragere
        self.assertEqual(run(self.bal("buyer")), 0)
        self.assertEqual(self.db.refund_requests.docs[0]["status"], "refunded")
        # esec definitiv: Astransii se returneaza
        run(self.db.refund_requests.delete_many({}))
        rid2 = run(setup("pi_t2"))
        self.stripe.mode = "reject"
        self.assertEqual(self.code(self.execute(rid2)), (502, "provider_rejected"))
        self.assertEqual(run(self.bal("buyer")), 300)
        self.assertEqual(self.db.refund_requests.docs[0]["status"], "approved")

    def test_store_provider_needs_external_confirmation(self):
        async def go():
            tx, _ = await self.seed_package_purchase(ref="GPA.1", provider="google_play")
            await self.db.users.update_one({"user_id": "buyer"}, {"$set": {"astrans_balance": 300}})
            rid = (await self.request_refund(self.buyer, tx["tx_id"], "defective"))["refund_id"]
            await self.decide(rid)
            return rid
        rid = run(go())
        out = run(self.execute(rid))
        self.assertEqual((out["status"], out["processing_state"]), ("approved", "awaiting_store_refund"))   # NU declaram "refunded" fara magazin
        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(self.code(self.call("POST", "/consumer/admin/refunds/{refund_id}/confirm-external", rid, NS(external_ref="x"), self.admin))[1], "reference_required")
        out = run(self.call("POST", "/consumer/admin/refunds/{refund_id}/confirm-external", rid, NS(external_ref="GPA.3301-1234"), self.admin))
        self.assertEqual(out["status"], "refunded")

    def test_refund_never_exceeds_paid_across_requests(self):
        async def go():
            tx, _ = await self.seed_package_purchase()
            await self.db.users.update_one({"user_id": "buyer"}, {"$set": {"astrans_balance": 100}})
            r1 = (await self.request_refund(self.buyer, tx["tx_id"], "defective"))["refund_id"]
            await self.decide(r1); await self.execute(r1)                                              # rambursat 100/300
            await self.db.users.update_one({"user_id": "buyer"}, {"$set": {"astrans_balance": 500}})   # a primit alte Astrans (gratuiti)
            r2 = (await self.request_refund(self.buyer, tx["tx_id"], "defective"))["refund_id"]
            await self.decide(r2)
            return r2
        run(go())
        plans = [r["plan"] for r in self.db.refund_requests.docs]
        self.assertEqual(plans[1]["astrans_to_reclaim"], 200)                                          # doar restul de 200, nu mai mult
        self.assertLessEqual(sum(p["money_refund_minor"] for p in plans), 1199)

    def test_10_admin_endpoints_are_authorized_on_backend(self):
        async def go():
            await self.bought_item()
            tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
            return (await self.request_refund(self.buyer, tx["tx_id"]))["refund_id"]
        rid = run(go())
        for who in (self.buyer, self.other, self.seller, {**self.buyer, "is_platform_admin": True}):   # inclusiv un token/obiect care "pretinde" ca e admin
            self.assertEqual(self.code(self.call("GET", "/consumer/admin/refunds", None, who))[0], 403)
            self.assertEqual(self.code(self.call("GET", "/consumer/admin/refunds/{refund_id}", rid, who))[0], 403)
            self.assertEqual(self.code(self.call("POST", "/consumer/admin/refunds/{refund_id}/review", rid, who))[0], 403)
            self.assertEqual(self.code(self.decide(rid, admin=who))[0], 403)
            self.assertEqual(self.code(self.execute(rid, admin=who))[0], 403)
            self.assertEqual(self.code(self.call("GET", "/consumer/admin/audit", None, who))[0], 403)
        self.assertEqual(self.db.refund_requests.docs[0]["status"], "pending")
        # adminul vede lista, trece cererea in analiza; nu poate decide asupra propriei cereri
        self.assertEqual(len(run(self.call("GET", "/consumer/admin/refunds", "pending", self.admin))["requests"]), 1)
        run(self.call("POST", "/consumer/admin/refunds/{refund_id}/review", rid, self.admin))
        self.assertEqual(self.db.refund_requests.docs[0]["status"], "under_review")
        run(self.db.refund_requests.update_one({"refund_id": rid}, {"$set": {"user_id": "admin"}}))
        self.assertEqual(self.code(self.decide(rid))[1], "conflict_of_interest")

    def test_audit_log_records_every_action(self):
        async def go():
            await self.bought_item()
            tx = [t for t in self.db.astran_ledger.docs if t["type"] == "shop_purchase"][0]
            rid = (await self.request_refund(self.buyer, tx["tx_id"]))["refund_id"]
            await self.call("POST", "/consumer/admin/refunds/{refund_id}/review", rid, self.admin)
            await self.call("GET", "/consumer/admin/refunds/{refund_id}", rid, self.admin)
            await self.decide(rid); await self.execute(rid)
            return rid
        rid = run(go())
        kinds = [a["event_type"] for a in self.db.consumer_audit_log.docs]
        for k in ("refund_requested", "review_started", "admin_viewed", "refund_approved", "execution_started", "refunded"):
            self.assertIn(k, kinds)
        self.assertTrue(all(a["refund_id"] == rid and a["actor_id"] for a in self.db.consumer_audit_log.docs))

    def test_stripe_processor_unit(self):
        p = R.StripeProcessor(http_post=self.stripe.post)
        a = run(p.refund(payment_intent="pi_1", amount_minor=500, refund_id="rf_x"))
        b = run(p.refund(payment_intent="pi_1", amount_minor=500, refund_id="rf_x"))
        self.assertTrue(a.ok and b.ok); self.assertEqual(a.provider_refund_id, b.provider_refund_id)   # aceeasi cheie => aceeasi rambursare
        saved = os.environ.pop("STRIPE_SECRET_KEY")
        try:
            r = run(p.refund(payment_intent="pi_1", amount_minor=500, refund_id="rf_y"))
            self.assertFalse(r.ok); self.assertTrue(r.transient)
        finally:
            os.environ["STRIPE_SECRET_KEY"] = saved


if __name__ == "__main__":
    unittest.main()
