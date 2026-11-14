"""python -m unittest astran_sandbox.test_scene_models

"Add to Game" (Shop Models -> scena Studio): rutele care stau in spatele butonului, rulate pe cod real:
  PUT/GET /sandbox/games/{id}/assets (?parts=true)  -> ataseaza si incarca modelul prin ACELASI resolver ca Assets.load
  GET /sandbox/games/{id}/scene-models              -> geometria modelelor referite de scena (pentru Play)
si drumul complet: salvarea scenei cu un obiect type="model" + reincarcarea ei (clean_scene).
Daca fastapi / pydantic nu sunt instalate, se folosesc stub-uri minime (rutele sunt apelate direct).
"""
import asyncio
import copy
import sys
import types
import unittest

try:  # pragma: no cover - mediu complet
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
except Exception:  # noqa: BLE001
    fa = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail

    class _Route:
        def __init__(self, path, methods, endpoint):
            self.path, self.methods, self.endpoint = path, methods, endpoint

    class APIRouter:
        def __init__(self, prefix="", tags=None, **kw):
            self.prefix, self.routes = prefix, []

        def _reg(self, method):
            def deco(path, **kw):
                def wrap(fn):
                    self.routes.append(_Route(self.prefix + path, {method}, fn))
                    return fn
                return wrap
            return deco

        def __getattr__(self, name):
            if name in ("get", "put", "post", "patch", "delete"):
                return self._reg(name.upper())
            raise AttributeError(name)

    fa.APIRouter, fa.HTTPException = APIRouter, HTTPException
    fa.Depends = lambda x=None: x
    sys.modules["fastapi"] = fa

    pd = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def Field(default=None, default_factory=None, **kw):
        return default_factory() if default_factory else default

    pd.BaseModel, pd.Field = BaseModel, Field
    sys.modules["pydantic"] = pd

from astran_sandbox.test_money import run  # noqa: E402,F401  (stub pymongo)
from astran_sandbox.routes import make_sandbox_router  # noqa: E402
from astran_sandbox.scene_validation import clean_scene  # noqa: E402


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, n=None):
        return self.docs[:n] if n else self.docs


class _Coll:
    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _ok(doc, flt):
        for k, c in flt.items():
            v = doc.get(k)
            if isinstance(c, dict) and "$in" in c:
                if v not in c["$in"]:
                    return False
            elif v != c:
                return False
        return True

    async def find_one(self, flt, proj=None):
        return next((copy.deepcopy(d) for d in self.docs if self._ok(d, flt)), None)

    def find(self, flt, proj=None):
        return _Cursor([copy.deepcopy(d) for d in self.docs if self._ok(d, flt)])

    async def update_one(self, flt, upd, upsert=False):
        for d in self.docs:
            if self._ok(d, flt):
                d.update(upd["$set"])
                return
        if upsert:
            nd = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            nd.update(upd["$set"])
            self.docs.append(nd)

    async def create_index(self, *a, **k):
        return None


PARTS = [
    {"id": "root", "name": "House", "type": "group", "parent": None, "x": 0, "y": 0, "z": 0, "rx": 0, "ry": 0, "rz": 0,
     "sx": 1, "sy": 1, "sz": 1, "color": "#cccccc", "material": "matte", "opacity": 1, "visible": True},
    {"id": "p1", "name": "Wall", "type": "cube", "parent": "root", "x": 0, "y": 1, "z": 0, "rx": 0, "ry": 0, "rz": 0,
     "sx": 4, "sy": 2, "sz": 3, "color": "#aa5533", "material": "matte", "opacity": 1, "visible": True},
]


def _item(item_id, owner="creator", price=0, public=True, parts=PARTS):
    return {"item_id": item_id, "kind": "model", "owner_id": owner, "name": "House " + item_id, "price": price,
            "is_public": public, "object": {"type": "cube", "color": "#aa5533", "scale": 1.0}, "model": {"parts": parts}}


class _DB:
    def __init__(self):
        self.shop_items = _Coll([
            _item("itm_free"),                       # gratis + public  => oricine il poate folosi
            _item("itm_paid", price=50),             # platit, necumparat
            _item("itm_bought", price=50),           # platit, cumparat de u1
        ])
        self.shop_purchases = _Coll([{"user_id": "u1", "item_id": "itm_bought"}])
        self.game_assets = _Coll()
        self.games = _Coll([{"game_id": "g1", "owner_id": "u1", "title": "My game", "scene": {"objects": []}}])
        self.game_scripts = _Coll()


def _endpoint(router, method, path):
    for r in router.routes:
        if r.path == path and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route {method} {path} not found")


class SceneModelsRouteTests(unittest.TestCase):
    def setUp(self):
        self.db = _DB()
        self.me = {"user_id": "u1", "username": "me", "age_category": "adult_18"}
        self.router = make_sandbox_router(lambda: self.me, self.db)
        self.put_assets = _endpoint(self.router, "PUT", "/sandbox/games/{game_id}/assets")
        self.get_assets = _endpoint(self.router, "GET", "/sandbox/games/{game_id}/assets")
        self.scene_models = _endpoint(self.router, "GET", "/sandbox/games/{game_id}/scene-models")

    def _body(self, ids):
        from astran_sandbox.routes import SaveAssetsBody
        return SaveAssetsBody(asset_ids=ids)

    def test_attach_then_load_real_model_with_parts(self):
        async def go():
            await self.put_assets("g1", self._body(["itm_free"]), current=self.me)
            plain = await self.get_assets("g1", parts=False, current=self.me)
            full = await self.get_assets("g1", parts=True, current=self.me)
            return plain, full
        plain, full = run(go())
        self.assertEqual(plain["asset_ids"], ["itm_free"])
        self.assertNotIn("parts", plain["assets"][0])          # Assets.load / scripturile: ca inainte, doar rezumatul
        self.assertEqual(plain["assets"][0]["object"]["type"], "cube")
        self.assertEqual(full["assets"][0]["id"], "itm_free")  # acelasi id real
        self.assertEqual([p["id"] for p in full["assets"][0]["parts"]], ["root", "p1"])

    def test_not_owned_model_is_not_loadable(self):
        async def go():
            await self.put_assets("g1", self._body(["itm_paid", "itm_bought", "itm_gone"]), current=self.me)
            return await self.get_assets("g1", parts=True, current=self.me)
        r = run(go())
        self.assertEqual([a["id"] for a in r["assets"]], ["itm_bought"])      # cumparat => ok
        self.assertEqual(sorted(r["missing_ids"]), ["itm_gone", "itm_paid"])  # necumparat / sters => eroare clara in client

    def test_attach_is_idempotent_no_duplicates(self):
        async def go():
            await self.put_assets("g1", self._body(["itm_free", "itm_free"]), current=self.me)
            await self.put_assets("g1", self._body(["itm_free", "itm_free"]), current=self.me)
            return await self.get_assets("g1", parts=False, current=self.me)
        r = run(go())
        self.assertEqual(r["asset_ids"], ["itm_free"])

    def test_scene_roundtrip_save_and_reload(self):
        """Obiectul model adaugat se salveaza in scena si, dupa 'redeschidere', geometria vine inapoi din /scene-models."""
        async def go():
            await self.put_assets("g1", self._body(["itm_free"]), current=self.me)
            saved = clean_scene({"objects": [
                {"id": "m1", "type": "model", "assetId": "itm_free", "name": "House itm_free", "x": 3, "y": 0, "z": 4, "ry": 45,
                 "scale": 2, "color": "#ffffff", "ex": 4, "ey": 2, "ez": 3, "visible": True, "solid": True}]})
            self.db.games.docs[0]["scene"] = saved                 # = PATCH /games/{id} {scene}
            reopened = copy.deepcopy(self.db.games.docs[0]["scene"])   # = GET /games/{id} la redeschidere
            models = await self.scene_models("g1", current=self.me)
            return reopened, models
        reopened, models = run(go())
        o = reopened["objects"][0]
        self.assertEqual((o["type"], o["assetId"], o["x"], o["z"], o["ry"], o["scale"]), ("model", "itm_free", 3.0, 4.0, 45.0, 2.0))
        self.assertEqual([m["id"] for m in models["models"]], ["itm_free"])
        self.assertEqual(len(models["models"][0]["parts"]), 2)
        self.assertEqual(models["missing_ids"], [])

    def test_scene_models_reports_missing_when_model_deleted(self):
        async def go():
            self.db.games.docs[0]["scene"] = clean_scene({"objects": [
                {"id": "m1", "type": "model", "assetId": "itm_gone"}, {"id": "m2", "type": "model", "assetId": "itm_paid"},
                {"id": "c", "type": "cube"}]})
            return await self.scene_models("g1", current=self.me)
        r = run(go())
        self.assertEqual(r["models"], [])
        self.assertEqual(sorted(r["missing_ids"]), ["itm_gone", "itm_paid"])


if __name__ == "__main__":
    unittest.main()