"""python -m unittest astran_sandbox.test_project_rev"""
import copy
import unittest

from astran_sandbox.test_money import DuplicateKeyError, run  # stub pymongo
from astran_sandbox.project_rev import ProjectConflict, save_project_doc


class _Scripts:
    """find_one_and_update cu upsert + index unic pe game_id, doar cat folosim."""
    def __init__(self):
        self.docs = []

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

    async def find_one_and_update(self, flt, upd, upsert=False, return_document=None, projection=None):
        for d in self.docs:
            if self._ok(d, flt):
                d.update(upd["$set"])
                for k, v in upd.get("$inc", {}).items():
                    d[k] = (d.get(k) or 0) + v
                return {"rev": d["rev"]}
        if not upsert:
            return None
        if any(d["game_id"] == upd["$set"]["game_id"] for d in self.docs):
            raise DuplicateKeyError("game_id")
        nd = {k: v for k, v in flt.items() if not isinstance(v, dict)}
        nd.update(upd["$set"])
        for k, v in upd.get("$inc", {}).items():
            nd[k] = (nd.get(k) or 0) + v
        self.docs.append(nd)
        return {"rev": nd["rev"]}


class _DB:
    def __init__(self):
        self.game_scripts = _Scripts()


F = [{"path": "main.lua", "source": "print(1)"}]


class ProjectRevTests(unittest.TestCase):
    def test_two_devices_second_save_is_rejected(self):
        async def go():
            db = _DB()
            r1 = await save_project_doc(db, "g", F, [], base_rev=0)        # proiect nou
            self.assertEqual(r1, 1)
            # A si B incarca amandoua rev=1
            ra = await save_project_doc(db, "g", [{"path": "a.lua", "source": "A"}], [], base_rev=1)
            self.assertEqual(ra, 2)
            with self.assertRaises(ProjectConflict):                        # B salveaza de la rev=1 invechit
                await save_project_doc(db, "g", [{"path": "b.lua", "source": "B"}], [], base_rev=1)
            self.assertEqual(db.game_scripts.docs[0]["files"][0]["source"], "A")   # munca lui A ramane
            self.assertEqual(await save_project_doc(db, "g", F, [], base_rev=2), 3)  # cu rev corect merge
        run(go())

    def test_first_save_conflicts_when_doc_already_exists(self):
        async def go():
            db = _DB()
            await save_project_doc(db, "g", F, [], base_rev=None)           # client vechi, creeaza rev=1
            with self.assertRaises(ProjectConflict):                        # cineva a creat deja; eu credeam ca e 0
                await save_project_doc(db, "g", F, [], base_rev=0)
        run(go())

    def test_legacy_client_without_base_rev_keeps_old_behaviour(self):
        async def go():
            db = _DB()
            await save_project_doc(db, "g", F, [], base_rev=None)
            r = await save_project_doc(db, "g", F, [], base_rev=None)       # fara verificare, dar rev creste
            self.assertEqual(r, 2)
        run(go())


if __name__ == "__main__":
    unittest.main()