"""Teste Shop: stergere fara rug-pull + anti-republicare. python -m unittest astran_sandbox.test_shop_rules"""
import asyncio
import copy
import unittest
from types import SimpleNamespace as NS

from astran_sandbox.test_money import DuplicateKeyError  # stub pymongo
from astran_sandbox import test_support_fakeweb as _fw
_fw.install()
from astran_sandbox import shop_routes  # noqa: E402
from astran_sandbox.shop_integrity import parts_hash, files_hash  # noqa: E402


def _ok(d, f):
    for k, c in f.items():
        v = d.get(k)
        if isinstance(c, dict):
            if "$ne" in c and v == c["$ne"]:
                return False
            if "$in" in c and v not in c["$in"]:
                return False
        elif v != c:
            return False
    return True


class Coll:
    def __init__(self):
        self.docs = []

    async def find_one(self, f, p=None):
        return next((copy.deepcopy(d) for d in self.docs if _ok(d, f)), None)

    async def insert_one(self, d):
        self.docs.append(copy.deepcopy(d))

    async def update_one(self, f, u, upsert=False):
        for d in self.docs:
            if _ok(d, f):
                d.update(u.get("$set", {}))
                return NS(matched_count=1)
        return NS(matched_count=0)

    async def delete_one(self, f):
        for i, d in enumerate(self.docs):
            if _ok(d, f):
                del self.docs[i]
                return

    async def create_index(self, *a, **k):
        pass

    def find(self, f, p=None):
        rows = [copy.deepcopy(d) for d in self.docs if _ok(d, f)]

        class Cur:
            def sort(s, *a, **k): return s
            def limit(s, n): return s
            async def to_list(s, n): return rows
        return Cur()


class DB:
    def __init__(self):
        self.shop_items, self.shop_purchases, self.studio_models = Coll(), Coll(), Coll()


def _parts(n, seed=0):
    return [{"id": f"p{i}", "type": "cube", "x": float(i + seed), "y": 0.0, "z": 0.0, "scale": 1.0, "color": "#ff0000"}
            for i in range(n)]


def _model(db, mid, owner, parts):
    db.studio_models.docs.append({"model_id": mid, "owner_id": owner, "parts": parts})


A = {"user_id": "A", "username": "alice"}
B = {"user_id": "B", "username": "bob"}
C = {"user_id": "C", "username": "carol"}


def _router(db):
    shop_routes.validate_parts = lambda p: p            # validarea geometriei e testata in alta parte
    shop_routes.shape_count = lambda p: len(p)
    shop_routes.summary_object = lambda p: {"n": len(p)}
    return shop_routes.make_shop_router(lambda: None, db).routes


class ShopRulesTests(unittest.TestCase):
    def test_sold_item_is_delisted_not_deleted_and_buyers_keep_it(self):
        async def go():
            db = DB()
            r = _router(db)
            db.shop_items.docs.append({"item_id": "i1", "kind": "model", "owner_id": "A", "owner_username": "alice",
                                       "name": "N", "description": "", "price": 50, "is_public": True, "downloads": 1,
                                       "model": {"parts": _parts(4)}, "object": {}, "part_count": 4,
                                       "created_at": None, "updated_at": None, "thumbnail_url": None})
            db.shop_purchases.docs.append({"user_id": "B", "item_id": "i1"})
            out = await r[("DELETE", "/shop/items/{item_id}")]("i1", A)
            self.assertTrue(out.get("delisted"))
            self.assertEqual(len(db.shop_items.docs), 1)                     # NU s-a sters
            # cumparatorul il vede in continuare, cu continut
            d = await r[("GET", "/shop/items/{item_id}")]("i1", B)
            self.assertIn("parts", d["item"])
            # un strain, autorul insusi si lista publica nu-l mai vad
            for who in (C, A):
                with self.assertRaises(shop_routes.HTTPException) as e:
                    await r[("GET", "/shop/items/{item_id}")]("i1", who)
                self.assertEqual(e.exception.status_code, 404)
            self.assertEqual((await r[("GET", "/shop/models")](None, False, C))["items"], [])
            self.assertEqual((await r[("GET", "/shop/models")](None, True, A))["items"], [])
            with self.assertRaises(shop_routes.HTTPException):               # nici cumparat de altii, nici editat
                await r[("POST", "/shop/items/{item_id}/buy")]("i1", C)
            with self.assertRaises(shop_routes.HTTPException) as e:
                await r[("PATCH", "/shop/items/{item_id}")]("i1", NS(name="x", description=None, price=None, is_public=True,
                                                                     thumbnail_url=None, model_id=None, files=None), A)
            self.assertEqual(e.exception.status_code, 404)
        asyncio.run(go())

    def test_unsold_item_is_really_deleted(self):
        async def go():
            db = DB()
            r = _router(db)
            db.shop_items.docs.append({"item_id": "i2", "kind": "model", "owner_id": "A", "price": 10, "is_public": True})
            out = await r[("DELETE", "/shop/items/{item_id}")]("i2", A)
            self.assertNotIn("delisted", out)
            self.assertEqual(db.shop_items.docs, [])
        asyncio.run(go())

    def test_exact_copy_of_other_authors_model_is_refused(self):
        async def go():
            db = DB()
            r = _router(db)
            _model(db, "mA", "A", _parts(5))
            body = lambda mid: NS(model_id=mid, name="m", description="", price=10, is_public=True, thumbnail_url=None)
            await r[("POST", "/shop/models")](body("mA"), A)                 # A publica originalul
            # B a cumparat-o, o copiaza in Studio-ul lui (alte id-uri) si incearca sa o publice
            copied = copy.deepcopy(_parts(5))
            for i, p in enumerate(copied):
                p["id"] = f"zz{i}"
            _model(db, "mB", "B", copied)
            with self.assertRaises(shop_routes.HTTPException) as e:
                await r[("POST", "/shop/models")](body("mB"), B)
            self.assertEqual(e.exception.status_code, 409)
            _model(db, "mB2", "B", _parts(5, seed=7))                         # model diferit: ok
            await r[("POST", "/shop/models")](body("mB2"), B)
            await r[("POST", "/shop/models")](body("mA"), A)                  # acelasi autor poate republica
        asyncio.run(go())

    def test_hashes(self):
        self.assertIsNone(parts_hash(_parts(2), 2))                          # prea mic: fara hash
        a = parts_hash(_parts(5), 5)
        self.assertEqual(a, parts_hash([{**p, "id": "x" + p["id"]} for p in _parts(5)], 5))   # id-urile nu conteaza
        self.assertNotEqual(a, parts_hash(_parts(5, seed=1), 5))
        self.assertIsNone(files_hash([{"name": "main", "source": "print(1)"}]))
        long_src = "local x = 1\n" * 30
        self.assertEqual(files_hash([{"name": "main", "source": long_src}]), files_hash([{"name": "main", "source": long_src + "  \n"}]))


if __name__ == "__main__":
    unittest.main()