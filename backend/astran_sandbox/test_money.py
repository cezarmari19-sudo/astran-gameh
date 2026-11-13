"""Teste pentru money.py (mutari de Astrans sigure la cadere).

Ruleaza fara MongoDB si fara pytest:  python -m unittest astran_sandbox.test_money
Foloseste un mini-DB in memorie care implementeaza doar operatorii folositi de money.py.
"""
import asyncio
import copy
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

try:  # pymongo poate lipsi in mediul de test
    from pymongo.errors import DuplicateKeyError
except Exception:  # noqa: BLE001
    pm = types.ModuleType("pymongo")
    pe = types.ModuleType("pymongo.errors")

    class DuplicateKeyError(Exception):
        pass

    pe.DuplicateKeyError = DuplicateKeyError

    class ReturnDocument:  # doar atat folosesc modulele testate (project_rev)
        BEFORE = False
        AFTER = True

    pm.ReturnDocument = ReturnDocument
    pm.errors = pe
    sys.modules["pymongo"] = pm
    sys.modules["pymongo.errors"] = pe

from astran_sandbox import money  # noqa: E402


def _get(doc, key):
    return doc.get(key)


def _match(doc, flt):
    for k, cond in flt.items():
        val = _get(doc, k)
        if isinstance(cond, dict) and any(c.startswith("$") for c in cond):
            for op, arg in cond.items():
                if op == "$gte" and not (val is not None and val >= arg):
                    return False
                if op == "$lt" and not (val is not None and val < arg):
                    return False
                if op == "$in" and val not in arg:
                    return False
                if op == "$ne":
                    if isinstance(val, list):
                        if arg in val:
                            return False
                    elif val == arg:
                        return False
        elif isinstance(val, list) and not isinstance(cond, list):
            if cond not in val:
                return False
        elif val != cond:
            return False
    return True


class _Res:
    def __init__(self, matched):
        self.matched_count = matched
        self.modified_count = matched
        self.deleted_count = matched


class FakeColl:
    def __init__(self, unique=()):
        self.docs = []
        self.unique = unique

    async def create_index(self, *a, **k):
        return None

    async def insert_one(self, doc):
        doc = copy.deepcopy(doc)
        for u in self.unique:
            if u in doc and any(d.get(u) == doc[u] for d in self.docs):
                raise DuplicateKeyError(u)
        self.docs.append(doc)

    async def find_one(self, flt, proj=None):
        for d in self.docs:
            if _match(d, flt):
                return copy.deepcopy(d)
        return None

    async def update_one(self, flt, upd, upsert=False):
        for d in self.docs:
            if _match(d, flt):
                for k, v in upd.get("$set", {}).items():
                    d[k] = v
                for k, v in upd.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                for k, v in upd.get("$push", {}).items():
                    arr = d.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                        if "$slice" in v and v["$slice"] < 0:
                            del arr[: max(0, len(arr) + v["$slice"])]
                    else:
                        arr.append(v)
                return _Res(1)
        return _Res(0)

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _match(d, flt):
                del self.docs[i]
                return _Res(1)
        return _Res(0)

    def find(self, flt, proj=None):
        outer = self

        class _Cur:
            async def to_list(self, n):
                return [copy.deepcopy(d) for d in outer.docs if _match(d, flt)][:n]

        return _Cur()


class FakeDB:
    def __init__(self):
        self.users = FakeColl(unique=("user_id",))
        self.astran_ledger = FakeColl(unique=("tx_id", "idempotency_key"))
        self.shop_purchases = FakeColl()
        self.groups = FakeColl(unique=("group_id", "owner_id"))
        self._j = FakeColl(unique=("op_id",))

    def __getitem__(self, name):
        return self._j if name == money.JOURNAL else getattr(self, name)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


async def _mkdb(buyer=100, seller=0):
    db = FakeDB()
    await db.users.insert_one({"user_id": "A", "astrans_balance": buyer})
    await db.users.insert_one({"user_id": "B", "astrans_balance": seller})
    return db


def _ledger(op, uid, kind, amount):
    return {"collection": "astran_ledger", "doc": {"tx_id": f"{op}-{uid}", "user_id": uid, "type": kind, "amount": amount}}


async def _begin_transfer(db, op="op1", amount=40, net=38):
    await money.begin(
        db, op_id=op, kind="transfer", payer_id="A", amount=amount,
        credits=[{"user_id": "B", "amount": net}],
        inserts=[_ledger(op, "A", "transfer_out", amount), _ledger(op, "B", "transfer_in", net)],
    )


def _bal(db, uid):
    return next(d for d in db.users.docs if d["user_id"] == uid)["astrans_balance"]


class MoneyTests(unittest.TestCase):
    def test_happy_path(self):
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            self.assertEqual(await money.run(db, "op1"), "done")
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (60, 38))
            self.assertEqual(len(db.astran_ledger.docs), 2)
        run(go())

    def test_insufficient_funds_changes_nothing(self):
        async def go():
            db = await _mkdb(buyer=10)
            await _begin_transfer(db)
            self.assertEqual(await money.run(db, "op1"), "insufficient")
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (10, 0))
            self.assertEqual(db.astran_ledger.docs, [])
        run(go())

    def test_run_twice_is_idempotent(self):
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            await money.run(db, "op1")
            await money.run(db, "op1")
            await money.settle(db, "op1")
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (60, 38))
            self.assertEqual(len(db.astran_ledger.docs), 2)
        run(go())

    def test_crash_after_debit_is_rolled_forward(self):
        """Debitul s-a aplicat, apoi procesul a picat: recuperarea trebuie sa plateasca destinatarul."""
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            self.assertEqual(await money.debit(db, "A", 40, "op1"), "ok")   # ... si cade aici
            db._j.docs[0]["created_at"] -= timedelta(minutes=10)
            self.assertEqual(await money.recover_pending(db), 1)
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (60, 38))
            self.assertEqual(len(db.astran_ledger.docs), 2)
            self.assertEqual(await money.recover_pending(db), 0)             # nu se aplica de doua ori
        run(go())

    def test_crash_in_the_middle_of_settle(self):
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            await money.debit(db, "A", 40, "op1")
            await money.credit(db, "B", 38, money._m("op1", "c", "B"))      # creditul s-a aplicat, ledger nu
            db._j.docs[0]["created_at"] -= timedelta(minutes=10)
            await money.recover_pending(db)
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (60, 38))      # B NU primeste dublu
            self.assertEqual(len(db.astran_ledger.docs), 2)
        run(go())

    def test_crash_before_debit_releases_reservation(self):
        """Randul de purchase rezervat, dar debitul nu s-a facut: nu ramane item gratis."""
        async def go():
            db = await _mkdb()
            res = {"collection": "shop_purchases", "filter": {"user_id": "A", "item_id": "it1"}}
            await money.begin(db, op_id="op2", kind="shop", payer_id="A", amount=50,
                              credits=[{"user_id": "B", "amount": 47}], reservations=[res])
            await db.shop_purchases.insert_one({"user_id": "A", "item_id": "it1"})
            db._j.docs[0]["created_at"] -= timedelta(minutes=10)
            await money.recover_pending(db)
            self.assertEqual(db.shop_purchases.docs, [])
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (100, 0))
        run(go())

    def test_losing_concurrent_buy_does_not_delete_winners_purchase(self):
        """Doua cumparari concurente: cea care pierde indexul unic NU are voie sa stearga randul castigatorului."""
        async def go():
            db = await _mkdb()
            for op in ("A1", "B1"):
                res = {"collection": "shop_purchases", "filter": {"user_id": "A", "item_id": "it1", "op_id": op}}
                await money.begin(db, op_id=op, kind="shop", payer_id="A", amount=10,
                                  credits=[{"user_id": "B", "amount": 9}], reservations=[res])
            await db.shop_purchases.insert_one({"user_id": "A", "item_id": "it1", "op_id": "A1"})
            await money.rollback(db, "B1", "already owned")            # B1 a pierdut cursa
            self.assertEqual(len(db.shop_purchases.docs), 1)            # randul lui A1 ramane
            self.assertEqual(await money.run(db, "A1"), "done")
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (90, 9))
        run(go())

    def test_purchase_credit_only_op(self):
        """Cumparare de Astrans (amount=0 debit): creditul si ledger-ul se aplica o singura data."""
        async def go():
            db = await _mkdb(buyer=0)
            await money.begin(db, op_id="buy1", kind="purchase", payer_id="A", amount=0,
                              credits=[{"user_id": "A", "amount": 500}],
                              inserts=[_ledger("buy1", "A", "purchase", 500)])
            await money.run(db, "buy1")
            await money.run(db, "buy1")
            self.assertEqual(_bal(db, "A"), 500)
            self.assertEqual(len(db.astran_ledger.docs), 1)
        run(go())

    def test_recent_pending_is_left_alone(self):
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            self.assertEqual(await money.recover_pending(db), 0)
        run(go())

    def test_rollback_after_debit_refunds_exactly_once(self):
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            await money.debit(db, "A", 40, "op1")
            await money.rollback(db, "op1", "test")
            await money.rollback(db, "op1", "test")
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (100, 0))
        run(go())

    def test_rollback_has_no_effect_after_done(self):
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            await money.run(db, "op1")
            await money.rollback(db, "op1", "late")
            self.assertEqual((_bal(db, "A"), _bal(db, "B")), (60, 38))
        run(go())

    def test_duplicate_op_id_rejected(self):
        async def go():
            db = await _mkdb()
            await _begin_transfer(db)
            with self.assertRaises(DuplicateKeyError):
                await _begin_transfer(db)
        run(go())



class RunFailedStateTests(unittest.TestCase):
    def test_run_on_rolled_back_op_raises_instead_of_pretending_ok(self):
        async def go():
            db = await _mkdb(buyer=100, seller=0)
            await db[money.JOURNAL].insert_one({"op_id": "opF2", "state": "failed", "payer_id": "A", "amount": 10})
            with self.assertRaises(money.MoneyOpFailed):
                await money.run(db, "opF2")
            self.assertEqual((await db.users.find_one({"user_id": "A"}))["astrans_balance"], 100)  # nimic debitat
        run(go())

if __name__ == "__main__":
    unittest.main()