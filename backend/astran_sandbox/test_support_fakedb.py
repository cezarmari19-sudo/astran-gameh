"""DB MongoDB simulat, in memorie, pentru teste fara MongoDB/pymongo.

Suporta operatorii folositi de privacy.py / project_security / email_verification:
  filtre: egalitate (si pe campuri-lista / cai cu punct), $in $nin $ne $exists $lt $lte $gt $gte $regex $type $or $and
  update: $set $unset $inc $push($each) $addToSet $setOnInsert (+ upsert)
Fiecare operatie cedeaza bucla de evenimente (await asyncio.sleep(0)) ca testele de concurenta sa intercaleze
cererile; corpul operatiei ramane atomic (la fel ca o operatie pe un singur document in MongoDB).
"""
import asyncio
import copy
import re
from datetime import datetime


class DuplicateKeyError(Exception):
    pass


_MISSING = object()


def _values(doc, path):
    """Valorile de la o cale cu punct; listele se desfac (semantica de potrivire Mongo)."""
    cur = [doc]
    for part in path.split("."):
        nxt = []
        for c in cur:
            if isinstance(c, list):
                for el in c:
                    if isinstance(el, dict) and part in el:
                        nxt.append(el[part])
            elif isinstance(c, dict) and part in c:
                nxt.append(c[part])
        cur = nxt
    out = []
    for v in cur:
        if isinstance(v, list):
            out.extend(v)
            out.append(v)
        else:
            out.append(v)
    return out


def _cmp_ok(v, op, arg):
    try:
        if op == "$lt": return v is not None and v < arg
        if op == "$lte": return v is not None and v <= arg
        if op == "$gt": return v is not None and v > arg
        if op == "$gte": return v is not None and v >= arg
    except TypeError:
        return False
    raise AssertionError(op)


def match(doc, flt):
    for k, cond in (flt or {}).items():
        if k == "$or":
            if not any(match(doc, c) for c in cond):
                return False
            continue
        if k == "$and":
            if not all(match(doc, c) for c in cond):
                return False
            continue
        vals = _values(doc, k)
        if isinstance(cond, dict) and cond and all(isinstance(c, str) and c.startswith("$") for c in cond):
            for op, arg in cond.items():
                if op == "$exists":
                    if bool(vals) != bool(arg):
                        return False
                elif op == "$in":
                    if not any((v in arg) for v in vals) and not (not vals and None in arg):
                        return False
                elif op == "$nin":
                    if any((v in arg) for v in vals) or (not vals and None in arg):
                        return False
                elif op == "$ne":
                    if any(v == arg for v in vals) or (not vals and arg is None):
                        return False
                elif op in ("$lt", "$lte", "$gt", "$gte"):
                    if not any(_cmp_ok(v, op, arg) for v in vals):
                        return False
                elif op == "$regex":
                    flags = re.I if "i" in (cond.get("$options") or "") else 0
                    if not any(isinstance(v, str) and re.search(arg, v, flags) for v in vals):
                        return False
                elif op == "$options":
                    pass
                elif op == "$type":
                    if arg == "date":
                        if not any(isinstance(v, datetime) for v in vals):
                            return False
                    else:
                        raise AssertionError("tip nesuportat " + str(arg))
                else:
                    raise AssertionError("operator nesuportat " + op)
        else:
            if cond is None:
                if vals and not any(v is None for v in vals):
                    return False
            elif not any(v == cond for v in vals):
                return False
    return True


def _set_path(doc, path, value):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _unset_path(doc, path):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.get(p)
        if not isinstance(cur, dict):
            return
    cur.pop(parts[-1], None)


def _get_path(doc, path, default=_MISSING):
    cur = doc
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def apply_update(doc, upd, inserting=False):
    for k, v in upd.get("$set", {}).items():
        _set_path(doc, k, copy.deepcopy(v))
    for k in upd.get("$unset", {}):
        _unset_path(doc, k)
    for k, v in upd.get("$inc", {}).items():
        cur = _get_path(doc, k, 0)
        _set_path(doc, k, (cur or 0) + v)
    for k, v in upd.get("$push", {}).items():
        lst = _get_path(doc, k, None)
        if lst is None:
            lst = []
            _set_path(doc, k, lst)
        items = v["$each"] if isinstance(v, dict) and "$each" in v else [v]
        lst.extend(items)
        if isinstance(v, dict) and "$slice" in v and v["$slice"] < 0:
            del lst[:max(0, len(lst) + v["$slice"])]
    for k, v in upd.get("$addToSet", {}).items():
        lst = _get_path(doc, k, None)
        if lst is None:
            lst = []
            _set_path(doc, k, lst)
        if v not in lst:
            lst.append(v)
    if inserting:
        for k, v in upd.get("$setOnInsert", {}).items():
            _set_path(doc, k, copy.deepcopy(v))


def _project(doc, proj):
    if not proj:
        return copy.deepcopy(doc)
    inc = {k for k, v in proj.items() if v and k != "_id"}
    if inc:
        return {k: copy.deepcopy(v) for k, v in doc.items() if k in inc}
    exc = {k for k, v in proj.items() if not v}
    return {k: copy.deepcopy(v) for k, v in doc.items() if k not in exc}


class Res:
    def __init__(self, matched=0, modified=0, deleted=0, upserted_id=None):
        self.matched_count, self.modified_count, self.deleted_count, self.upserted_id = matched, modified, deleted, upserted_id


class Cursor:
    def __init__(self, docs, proj):
        self.docs, self.proj = docs, proj

    def sort(self, key, direction=1):
        pairs = key if isinstance(key, list) else [(key, direction)]
        for k, d in reversed(pairs):
            self.docs.sort(key=lambda x: (x.get(k) is None, x.get(k)), reverse=(d == -1))
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, n=None):
        await asyncio.sleep(0)
        return [_project(d, self.proj) for d in (self.docs if n is None else self.docs[:n])]


class Collection:
    def __init__(self, name):
        self.name = name
        self.docs = []
        self.unique = []

    def _check_unique(self, doc, ignore=None):
        for fields in self.unique:
            if any(f not in doc or doc[f] is None for f in fields):
                continue
            for d in self.docs:
                if d is ignore:
                    continue
                if all(d.get(f) == doc.get(f) for f in fields):
                    raise DuplicateKeyError(f"{self.name}:{fields}")

    async def create_index(self, spec, unique=False, **kw):
        await asyncio.sleep(0)
        if unique:
            fields = (spec,) if isinstance(spec, str) else tuple(x[0] for x in spec)
            self.unique.append(fields)

    async def insert_one(self, doc):
        await asyncio.sleep(0)
        d = copy.deepcopy(doc)
        self._check_unique(d)
        self.docs.append(d)
        return Res()

    def find(self, flt=None, proj=None):
        return Cursor([d for d in self.docs if match(d, flt)], proj)

    async def find_one(self, flt=None, proj=None):
        await asyncio.sleep(0)
        for d in self.docs:
            if match(d, flt):
                return _project(d, proj)
        return None

    async def count_documents(self, flt):
        await asyncio.sleep(0)
        return sum(1 for d in self.docs if match(d, flt))

    async def update_one(self, flt, upd, upsert=False):
        await asyncio.sleep(0)
        for d in self.docs:
            if match(d, flt):
                apply_update(d, upd)
                return Res(1, 1)
        if upsert:
            nd = {k: v for k, v in flt.items() if not (isinstance(v, dict) or k.startswith("$"))}
            apply_update(nd, upd, inserting=True)
            self._check_unique(nd)
            self.docs.append(nd)
            return Res(0, 0, upserted_id="x")
        return Res()

    async def update_many(self, flt, upd):
        await asyncio.sleep(0)
        n = 0
        for d in self.docs:
            if match(d, flt):
                apply_update(d, upd)
                n += 1
        return Res(n, n)

    async def find_one_and_update(self, flt, upd, projection=None, return_document=None, upsert=False):
        await asyncio.sleep(0)
        for d in self.docs:
            if match(d, flt):
                before = _project(d, projection)
                apply_update(d, upd)
                return before
        return None

    async def delete_one(self, flt):
        await asyncio.sleep(0)
        for i, d in enumerate(self.docs):
            if match(d, flt):
                del self.docs[i]
                return Res(deleted=1)
        return Res()

    async def delete_many(self, flt):
        await asyncio.sleep(0)
        keep = [d for d in self.docs if not match(d, flt)]
        n = len(self.docs) - len(keep)
        self.docs[:] = keep
        return Res(deleted=n)


class FakeDB:
    def __init__(self):
        self._c = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name):
        if name not in self._c:
            self._c[name] = Collection(name)
        return self._c[name]

    def all_docs(self):
        for name, c in self._c.items():
            for d in c.docs:
                yield name, d