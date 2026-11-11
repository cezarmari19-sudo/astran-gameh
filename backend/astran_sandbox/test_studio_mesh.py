import sys, types, importlib.util, pathlib
# fastapi/pydantic pot lipsi in sandbox: le inlocuim doar daca nu exista
try:
    import fastapi, pydantic  # noqa
except ImportError:
    fa = types.ModuleType("fastapi"); fa.APIRouter = object; fa.Depends = lambda *a, **k: None; fa.HTTPException = Exception
    pd = types.ModuleType("pydantic"); pd.BaseModel = object; pd.Field = lambda *a, **k: None
    sys.modules["fastapi"] = fa; sys.modules["pydantic"] = pd
pkg = types.ModuleType("astran_sandbox"); pkg.__path__ = ["" + str(__import__("pathlib").Path(__file__).parent) + ""]; sys.modules["astran_sandbox"] = pkg
spec = importlib.util.spec_from_file_location("astran_sandbox.studio_routes", "" + str(__import__("pathlib").Path(__file__).parent) + "/studio_routes.py")
sr = importlib.util.module_from_spec(spec); sys.modules[spec.name] = sr; spec.loader.exec_module(sr)

def part(pid, t="cube", parent="root", **kw):
    d = dict(id=pid, name=pid, type=t, parent=parent, x=0, y=0, z=0, rx=0, ry=0, rz=0, sx=1, sy=1, sz=1, color="#ff0000", material="matte", opacity=1, visible=True)
    d.update(kw); return d
root = part("root", "group", None)
cube = {"v": [-.5,-.5,-.5, .5,-.5,-.5, .5,.5,-.5, -.5,.5,-.5, -.5,-.5,.5, .5,-.5,.5, .5,.5,.5, -.5,.5,.5],
        "f": [[4,5,6,7],[1,0,3,2],[5,1,2,6],[0,4,7,3],[7,6,2,3],[0,1,5,4]]}
ok = 0
def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name); ok += 0 if cond else 1
out = sr.validate_parts([root, part("m1", "mesh", mesh=cube)])
check("mesh valid se pastreaza", out[1]["mesh"]["f"] == cube["f"] and len(out[1]["mesh"]["v"]) == 24)
check("piesele primitive nu au cheia mesh", "mesh" not in sr.validate_parts([root, part("a")])[1])
def bad(m, label):
    try: sr.validate_parts([root, part("m1", "mesh", mesh=m)]); check(label, False)
    except ValueError: check(label, True)
bad(None, "mesh lipsa respins")
bad({"v": [0,0,0], "f": [[0,1,2]]}, "index in afara respins")
bad({"v": [0,0,0,1,0,0,0,1,0], "f": [[0,0,1]]}, "varf repetat respins")
bad({"v": [0,0,0,1,0,0,0,1,0], "f": [[0,1]]}, "fata cu <3 varfuri respinsa")
bad({"v": [0,0,float("nan"),1,0,0,0,1,0], "f": [[0,1,2]]}, "NaN respins")
bad({"v": [0,0,1e9,1,0,0,0,1,0], "f": [[0,1,2]]}, "coordonata uriasa respinsa")
bad({"v": [0,0,0,1,0,0,0,1], "f": [[0,1,2]]}, "lungime v % 3 respinsa")
bad({"v": [0,0,0]*6001, "f": [[0,1,2]]}, "prea multe varfuri respins")
bad({"v": ["a",0,0,1,0,0,0,1,0], "f": [[0,1,2]]}, "coordonata non-numerica respinsa")
bad({"v": [0,0,0,1,0,0,0,1,0], "f": [[0,1,True]]}, "index bool respins")
try:
    sr.validate_parts([root] + [part(f"m{i}", "mesh", mesh={"v": [0,0,0,1,0,0,0,1,0]*1000, "f": [[0,1,2]]}) for i in range(8)]); check("buget total respins", False)
except ValueError: check("buget total de varfuri respins", True)
check("summary_object cu mesh", sr.summary_object(out)["type"] == "cube" and sr.shape_count(out) == 1)
print("\nFAIL" if ok else "\nOK"); sys.exit(1 if ok else 0)