"""Stub minimal fastapi/pydantic: permite importul modulelor de rute si apelul direct al handler-elor (teste)."""
import sys, types, copy, asyncio
def install():
    """Instaleaza stub-uri fastapi/pydantic DOAR daca pachetele reale lipsesc (mediu de test minimal)."""
    try:
        import fastapi, pydantic  # noqa: F401
        return
    except Exception:  # noqa: BLE001
        pass
    if "fastapi" in sys.modules and getattr(sys.modules["fastapi"], "_fake", False): return
    fa=types.ModuleType("fastapi"); fa._fake=True
    class HTTPException(Exception):
        def __init__(self,status_code,detail=None,headers=None): super().__init__(detail); self.status_code=status_code; self.detail=detail
    class APIRouter:
        def __init__(self,prefix="",tags=None): self.prefix=prefix; self.routes={}
        def _reg(self,m):
            def deco(path,**k):
                def d(f): self.routes[(m,self.prefix+path)]=f; return f
                return d
            return deco
        def include_router(self,r,**k): self.routes.update(r.routes)
        def __getattr__(self,n):
            if n in ("get","post","put","patch","delete"): return self._reg(n.upper())
            raise AttributeError(n)
    fa.HTTPException=HTTPException; fa.APIRouter=APIRouter; fa.Depends=lambda x=None: x
    fa.Request=object; fa.WebSocket=object; fa.Query=lambda *a,**k: None
    sys.modules["fastapi"]=fa
    pd=types.ModuleType("pydantic")
    class BaseModel:
        def __init__(self,**k): self.__dict__.update(k)
        def dict(self,**k): return dict(self.__dict__)
    pd.BaseModel=BaseModel; pd.Field=lambda *a,default=None,**k: default
    pd.EmailStr=str; pd.validator=lambda *a,**k:(lambda f:f); pd.field_validator=pd.validator; pd.ConfigDict=dict
    sys.modules["pydantic"]=pd