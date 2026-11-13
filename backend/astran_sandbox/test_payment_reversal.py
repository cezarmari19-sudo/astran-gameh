"""Teste refund/chargeback. Ruleaza:  python -m unittest astran_sandbox.test_payment_reversal"""
import hashlib
import hmac
import unittest

from astran_sandbox.test_money import FakeDB, run  # intai: instaleaza stub-ul pymongo daca lipseste
from astran_sandbox import money, payment_reversal as pr  # noqa: E402


def _sig(payload: bytes, secret: str, ts: int) -> str:
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()


class StripeSignatureTests(unittest.TestCase):
    P = b'{"id":"evt_1"}'

    def test_valid_and_invalid(self):
        self.assertTrue(pr.verify_stripe_signature(self.P, _sig(self.P, "whsec", 1000), "whsec", now=1100))
        self.assertFalse(pr.verify_stripe_signature(self.P, _sig(self.P, "other", 1000), "whsec", now=1100))
        self.assertFalse(pr.verify_stripe_signature(self.P + b" ", _sig(self.P, "whsec", 1000), "whsec", now=1100))

    def test_replay_and_garbage(self):
        self.assertFalse(pr.verify_stripe_signature(self.P, _sig(self.P, "whsec", 1000), "whsec", now=1000 + 301))
        self.assertFalse(pr.verify_stripe_signature(self.P, _sig(self.P, "whsec", 5000), "whsec", now=1000))
        for h in ("", "garbage", "t=abc,v1=00", "v1=00"):
            self.assertFalse(pr.verify_stripe_signature(self.P, h, "whsec", now=1000))
        self.assertFalse(pr.verify_stripe_signature(self.P, _sig(self.P, "", 1000), "", now=1000))  # fara secret

    def test_multiple_v1(self):
        h = _sig(self.P, "whsec", 1000) + ",v1=deadbeef"
        self.assertTrue(pr.verify_stripe_signature(self.P, h, "whsec", now=1000))


class EventSelectionTests(unittest.TestCase):
    def test_events(self):
        full = {"type": "charge.refunded", "data": {"object": {"amount": 299, "amount_refunded": 299, "payment_intent": "pi_1"}}}
        part = {"type": "charge.refunded", "data": {"object": {"amount": 299, "amount_refunded": 100, "payment_intent": "pi_1"}}}
        disp = {"type": "charge.dispute.created", "data": {"object": {"payment_intent": "pi_2"}}}
        self.assertEqual(pr.stripe_reversal_ref(full), ("pi_1", "stripe_refund"))
        self.assertIsNone(pr.stripe_reversal_ref(part))
        self.assertEqual(pr.stripe_reversal_ref(disp), ("pi_2", "stripe_dispute"))
        self.assertIsNone(pr.stripe_reversal_ref({"type": "payment_intent.succeeded", "data": {"object": {}}}))
        self.assertIsNone(pr.stripe_reversal_ref("x"))

    def test_google_refs(self):
        d = {"voidedPurchases": [{"purchaseToken": "t1"}, {"purchaseToken": ""}, {"x": 1}, {"purchaseToken": "t2"}]}
        self.assertEqual(pr.google_voided_refs(d), ["t1", "t2"])
        self.assertEqual(pr.google_voided_refs(None), [])


async def _bought(balance_after_spend=10):
    db = FakeDB()
    await db.users.insert_one({"user_id": "U", "astrans_balance": 60})
    await db.astran_ledger.insert_one({"tx_id": "tx_buy", "user_id": "U", "type": "purchase", "amount": 60,
                                       "status": "completed", "provider_ref": "pi_1"})
    await money.debit(db, "U", 60 - balance_after_spend, "spend1")  # a cheltuit deja o parte
    return db


class ReverseTests(unittest.TestCase):
    def test_spent_astrans_are_clawed_back_once(self):
        async def go():
            db = await _bought(10)
            self.assertEqual(await pr.reverse_purchase(db, "pi_1", "stripe_refund"), "reversed")
            self.assertEqual((await db.users.find_one({"user_id": "U"}))["astrans_balance"], 10 - 60)
            self.assertEqual(await pr.reverse_purchase(db, "pi_1", "stripe_refund"), "already")  # webhook dublu
            self.assertEqual((await db.users.find_one({"user_id": "U"}))["astrans_balance"], -50)
            self.assertEqual((await db.astran_ledger.find_one({"tx_id": "tx_buy"}))["status"], "reversed")
            self.assertEqual(len([d for d in db.astran_ledger.docs if d["type"] == "purchase_reversal"]), 1)
            # sold negativ: nu mai poate cheltui nimic
            self.assertEqual(await money.debit(db, "U", 1, "spend2"), "insufficient")
        run(go())

    def test_unknown_ref(self):
        async def go():
            self.assertEqual(await pr.reverse_purchase(await _bought(), "pi_nope", "x"), "not_found")
        run(go())


if __name__ == "__main__":
    unittest.main()