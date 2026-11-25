"""Inlocuitori MINIMALI (doar pentru teste) pentru `fastapi`, `pydantic` si `pymongo.errors`, folositi NUMAI cand
pachetele reale nu sunt instalate (ex. un mediu CI fara dependinte). Cand sunt instalate, `install_if_missing()` nu
face nimic: testele ruleaza atunci pe FastAPI/pydantic/pymongo reale.

Scop: sa putem apela direct handler-ele reale din group_routes.py / realtime.py (nu copii ale logicii lor).
NU implementeaza HTTP, injectarea de dependente sau validarea completa - doar ce folosesc rutele testate.
"""
from __future__ import annotations

import sys
import types


def _have(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def install_if_missing() -> list:
    """Intoarce lista modulelor inlocuite (goala daca totul e instalat)."""
    stubbed = []
    from astran_sandbox.test_support_fakedb import DuplicateKeyError

    if not _have("pymongo"):
        pm, err = types.ModuleType("pymongo"), types.ModuleType("pymongo.errors")
        err.DuplicateKeyError = DuplicateKeyError
        pm.errors = err
        sys.modules["pymongo"], sys.modules["pymongo.errors"] = pm, err
        stubbed.append("pymongo")

    if not _have("pydantic"):
        pyd = types.ModuleType("pydantic")

        class ValidationError(ValueError):
            pass

        class _FieldInfo:
            def __init__(self, default=..., **kw):
                self.default, self.kw = default, kw

        def Field(default=..., **kw):
            return _FieldInfo(default, **kw)

        class BaseModel:
            model_config: dict = {}

            def __init__(self, **data):
                ann: dict = {}
                for klass in reversed(type(self).__mro__):
                    ann.update(getattr(klass, "__annotations__", {}))
                ann.pop("model_config", None)
                if type(self).model_config.get("extra") == "forbid":
                    extra = set(data) - set(ann)
                    if extra:
                        raise ValidationError("extra fields not permitted: %s" % sorted(extra))
                self._fields, self._set = list(ann), set()
                for name in ann:
                    default = getattr(type(self), name, ...)
                    kw = {}
                    if isinstance(default, _FieldInfo):
                        kw, default = default.kw, default.default
                    if name in data:
                        value = data[name]
                        self._set.add(name)
                    elif default is ...:
                        raise ValidationError("%s is required" % name)
                    else:
                        value = default
                    if isinstance(value, str):
                        if "min_length" in kw and len(value) < kw["min_length"]:
                            raise ValidationError("%s too short" % name)
                        if "max_length" in kw and len(value) > kw["max_length"]:
                            raise ValidationError("%s too long" % name)
                    setattr(self, name, value)

            def dict(self, exclude_unset: bool = False, **_):
                return {n: getattr(self, n) for n in self._fields if not exclude_unset or n in self._set}

        pyd.BaseModel, pyd.Field, pyd.ValidationError = BaseModel, Field, ValidationError
        sys.modules["pydantic"] = pyd
        stubbed.append("pydantic")

    if not _have("fastapi"):
        fa = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail=None):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail

        class WebSocketDisconnect(Exception):
            def __init__(self, code: int = 1000):
                super().__init__(code)
                self.code = code

        class WebSocket:  # marcaj de tip; testele folosesc un FakeWS
            pass

        class _Route:
            def __init__(self, path, methods, endpoint):
                self.path, self.methods, self.endpoint = path, set(methods), endpoint

        class APIRouter:
            def __init__(self, prefix: str = "", tags=None):
                self.prefix, self.routes = prefix, []

            def _reg(self, method):
                def outer(path, *a, **k):
                    def deco(fn):
                        self.routes.append(_Route(self.prefix + path, {method}, fn))
                        return fn
                    return deco
                return outer

            def __getattr__(self, name):
                if name in ("get", "post", "patch", "put", "delete"):
                    return self._reg(name.upper())
                if name == "websocket":
                    return self._reg("WS")
                raise AttributeError(name)

        fa.HTTPException, fa.WebSocketDisconnect, fa.WebSocket, fa.APIRouter = HTTPException, WebSocketDisconnect, WebSocket, APIRouter
        fa.Depends = lambda dep=None: ("depends", dep)
        fa.Query = lambda *a, **k: None
        sys.modules["fastapi"] = fa
        stubbed.append("fastapi")
    return stubbed


def endpoint(router, method: str, path: str):
    """Handler-ul real inregistrat pe `router` pentru (metoda, cale) - functioneaza si cu FastAPI real."""
    for r in router.routes:
        if getattr(r, "path", None) == path and method in {m.upper() for m in (getattr(r, "methods", None) or {"WS"})}:
            return r.endpoint
        if method == "WS" and getattr(r, "path", None) == path and not getattr(r, "methods", None):
            return r.endpoint
    raise LookupError(f"{method} {path}")
