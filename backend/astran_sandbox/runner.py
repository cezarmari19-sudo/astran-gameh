"""Ruleaza scripturi Luau (unul sau mai multe fisiere) in procesul izolat `luau-sandbox`.

Nu depinde de FastAPI, doar de biblioteca standard.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import signal
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).parent
PRELUDE = HERE / "prelude.luau"
DEFAULT_BINARY = HERE / "bin" / "luau-sandbox"

ENTRY_NAME = "main"          # fisierul care ruleaza primul
MAX_FILES = 32
MAX_NAME_CHARS = 64
MAX_SOURCE_CHARS = 20000     # per fisier
MAX_TOTAL_CHARS = 100000     # toate fisierele la un loc
MAX_OPS = 5000
MAX_LOG_LINES = 200
MAX_STDOUT_BYTES = 4 * 1024 * 1024
MAX_CONCURRENT_RUNS = 4
CACHE_SIZE = 128

SHAPE_TYPES = {"cube", "sphere", "cylinder", "cone", "pyramid"}
OP_KINDS = ("create", "set", "destroy", "world", "material")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")       # id de obiect (din script sau din scena Studio)
_MATERIAL_ID_RE = re.compile(r"^m[0-9]{1,4}$")         # id de material: m1, m2, ...

import logging

log = logging.getLogger("astran.sandbox.runner")


class SandboxUnavailable(RuntimeError):
    """Binarul luau-sandbox nu este instalat pe server."""


# ---------- fisiere ----------

# Numele rezervate pentru fisierele pe care le adauga serverul (nu scriptul jocului).
# Host-ul (host.cpp, validName) accepta doar litere, cifre, _ - si /, deci NU pot incepe cu "@".
ASSETS_FILE_NAME = "__assets"  # citit de prelude.luau pentru Assets.load
SCENE_FILE_NAME = "__scene"    # citit de prelude.luau: obiectele facute in Studio
RESERVED_NAMES = {ASSETS_FILE_NAME, SCENE_FILE_NAME}


def validate_files(files: Any) -> list[dict]:
    """Verifica lista de fisiere {name, source} si intoarce o copie curata. Ridica ValueError."""
    if not isinstance(files, list):
        raise ValueError("files must be a list")
    if len(files) > MAX_FILES:
        raise ValueError(f"Too many scripts (max {MAX_FILES})")

    clean: list[dict] = []
    seen: set[str] = set()
    total = 0
    for f in files:
        if not isinstance(f, dict):
            raise ValueError("Invalid script entry")
        name = f.get("name")
        source = f.get("source", "")
        if not isinstance(name, str) or not isinstance(source, str):
            raise ValueError("Invalid script entry")
        if len(name) > MAX_NAME_CHARS or not _NAME_RE.match(name):
            raise ValueError(f"Invalid script name '{name[:40]}' (use letters, numbers, _ - and /)")
        if name in RESERVED_NAMES:
            raise ValueError(f"The script name '{name}' is reserved")
        if name in seen:
            raise ValueError(f"Duplicate script name '{name}'")
        if len(source) > MAX_SOURCE_CHARS:
            raise ValueError(f"Script '{name}' is too long (max {MAX_SOURCE_CHARS} characters)")
        seen.add(name)
        total += len(source)
        clean.append({"name": name, "source": source})

    if total > MAX_TOTAL_CHARS:
        raise ValueError(f"Scripts are too long together (max {MAX_TOTAL_CHARS} characters)")
    return clean


def files_from_source(source: str) -> list[dict]:
    """Un singur script (compatibilitate) = fisierul 'main'."""
    return [{"name": ENTRY_NAME, "source": source}]


MAX_ASSETS = 200


def assets_file(assets: list[dict]) -> Optional[dict]:
    """Construieste fisierul special "__assets" din modelele cumparate de joc.

    `assets` = [{"id": str, "name": str, "object": {"type", "color", "scale"}}, ...]
    Datele sunt deja curatate de shop_routes.py inainte sa ajunga aici; tot facem
    o verificare minima, ca sa nu trimitem catre Luau ceva neasteptat.
    """
    if not assets:
        return None
    clean = []
    for a in assets[:MAX_ASSETS]:
        if not isinstance(a, dict):
            continue
        aid, name, obj = a.get("id"), a.get("name"), a.get("object")
        if not isinstance(aid, str) or not isinstance(obj, dict):
            continue
        clean.append({"id": aid, "name": name if isinstance(name, str) else "", "object": obj})
    if not clean:
        return None
    # ensure_ascii=False: numele cu diacritice (ă, î, ș...) raman octeti UTF-8 direct in
    # string, nu \uXXXX — decodorul JSON scris de mana in Luau (prelude.luau) nu reconstruieste
    # UTF-8 din \uXXXX, doar il inlocuieste cu "?"; ca octeti bruti insa trec neschimbati.
    payload = json.dumps(clean, ensure_ascii=False)
    return {"name": ASSETS_FILE_NAME, "source": "return " + json.dumps(payload, ensure_ascii=False)}


MAX_SCENE_OBJECTS = 1000
_SCENE_NAME_JUNK = re.compile(r"[^\w\- .]")


def scene_file(scene: Any) -> Optional[dict]:
    """Construieste fisierul special "__scene" cu obiectele facute in Studio, ca scriptul sa le poata alege.

    Obiectele din Studio nu au (inca) nume, deci primesc unul automat dupa tip si numarul lor de ordine:
    Cube1, Cube2, Sphere1, Cylinder1, Cone1, Tree1... Daca un obiect are un camp "name", se foloseste acela.
    Markerul "spawn" nu se trimite.
    """
    if not isinstance(scene, dict):
        return None
    objects = scene.get("objects")
    if not isinstance(objects, list) or not objects:
        return None

    counters: dict[str, int] = {}
    clean = []
    for o in objects[:MAX_SCENE_OBJECTS]:
        if not isinstance(o, dict):
            continue
        oid, otype = o.get("id"), o.get("type")
        if not isinstance(oid, str) or not _ID_RE.match(oid) or not isinstance(otype, str):
            continue
        if otype == "spawn":
            continue
        counters[otype] = counters.get(otype, 0) + 1

        custom = o.get("name")
        name = _SCENE_NAME_JUNK.sub("", custom).strip()[:64] if isinstance(custom, str) else ""
        if not name:
            name = f"{otype.capitalize()[:20]}{counters[otype]}"

        entry: dict = {"id": oid, "name": name, "type": otype[:20]}
        for key, default in (("x", 0.0), ("y", 0.0), ("z", 0.0), ("scale", 1.0), ("sx", 1.0), ("sy", 1.0), ("sz", 1.0)):
            n = _number(o.get(key))
            entry[key] = n if n is not None else default
        color = o.get("color")
        entry["color"] = color.lower() if isinstance(color, str) and _HEX_COLOR.match(color) else "#a3a3a3"
        entry["solid"] = o.get("solid") is not False
        entry["visible"] = o.get("visible") is not False
        clean.append(entry)

    if not clean:
        return None
    payload = json.dumps(clean, ensure_ascii=False)
    return {"name": SCENE_FILE_NAME, "source": "return " + json.dumps(payload, ensure_ascii=False)}


def _bundle(files: list[dict], assets: Optional[list[dict]] = None, scene: Any = None) -> bytes:
    all_files = list(files)
    for extra in (assets_file(assets) if assets else None, scene_file(scene)):
        if extra is not None:
            all_files.append(extra)

    parts: list[bytes] = []
    for f in all_files:
        data = f["source"].encode("utf-8")
        parts.append(b"@@FILE " + f["name"].encode("ascii") + b" " + str(len(data)).encode("ascii") + b"\n")
        parts.append(data)
        parts.append(b"\n")
    return b"".join(parts)


def _cache_key(files: list[dict], assets: Optional[list[dict]] = None, scene: Any = None) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f["name"].encode("utf-8"))
        h.update(b"\0")
        data = f["source"].encode("utf-8")
        h.update(str(len(data)).encode("ascii"))
        h.update(b"\0")
        h.update(data)
    extra = assets_file(assets) if assets else None
    if extra is not None:
        h.update(b"\0" + ASSETS_FILE_NAME.encode("ascii") + b"\0")
        h.update(extra["source"].encode("utf-8"))
    extra_scene = scene_file(scene)
    if extra_scene is not None:
        h.update(b"\0" + SCENE_FILE_NAME.encode("ascii") + b"\0")
        h.update(extra_scene["source"].encode("utf-8"))
    return h.hexdigest()


# ---------- proces ----------

def find_binary() -> Optional[str]:
    candidates = [os.environ.get("LUAU_SANDBOX_BIN"), str(DEFAULT_BINARY), shutil.which("luau-sandbox")]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _make_preexec(timeout_ms: int):
    """Limite de sistem aplicate procesului copil (doar Linux/macOS)."""

    def _apply() -> None:
        try:
            import resource
        except ImportError:
            return
        cpu = max(1, timeout_ms // 1000 + 2)
        gib = 1024 * 1024 * 1024
        limits = [
            (resource.RLIMIT_CPU, (cpu, cpu)),
            (resource.RLIMIT_CORE, (0, 0)),
            (resource.RLIMIT_FSIZE, (0, 0)),
            (resource.RLIMIT_NOFILE, (64, 64)),
            (resource.RLIMIT_AS, (gib, gib)),
        ]
        for which, value in limits:
            try:
                resource.setrlimit(which, value)
            except (ValueError, OSError):
                pass

    return _apply


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


# ---------- jail OS suplimentar (bubblewrap) ----------
#
# Tot ce e mai jos e un strat de izolare IN PLUS fata de cel existent (rlimit + env
# gol), nu un inlocuitor: Luau insusi nu ofera scriptului nicio cale spre OS (fara
# io/os.execute/require real - vezi host.cpp), deci un script "normal", care respecta
# doar limbajul, e oricum blocat. Stratul de aici e pentru cazul (rar) al unui bug de
# corupere de memorie chiar in interpretorul/compilatorul Luau: daca un script ar
# reusi, printr-un asemenea bug, sa execute cod nativ, acest cod s-ar trezi intr-un
# proces fara retea, intr-un filesystem minimal READ-ONLY (doar binarul si preludiul),
# rulat ca utilizatorul neprivilegiat "nobody" - nu in filesystem-ul real al
# serverului (unde sunt .env, codul sursa, etc).
#
# Daca `bwrap` (bubblewrap) nu e instalat SAU namespace-urile neprivilegiate sunt
# blocate de kernel/politica containerului, scriptele tot ruleaza normal - izolate
# exact ca pana acum (rlimit + env gol) - doar fara acest strat suplimentar. Vezi
# astran_sandbox/Dockerfile pentru cum se instaleaza bubblewrap la build.

_bwrap_checked = False
_bwrap_path: Optional[str] = None


async def _check_bwrap() -> Optional[str]:
    """Verifica o singura data (prima rulare) daca bubblewrap chiar functioneaza in
    acest mediu - nu doar daca binarul exista pe disc. Rezultatul e memorat pentru
    restul vietii procesului (nu se schimba intre doua request-uri)."""
    global _bwrap_checked, _bwrap_path
    if _bwrap_checked:
        return _bwrap_path
    _bwrap_checked = True

    bwrap = shutil.which("bwrap")
    if bwrap is None:
        log.warning(
            "bubblewrap (bwrap) nu e instalat - sandbox-ul Luau ruleaza fara jail-ul "
            "suplimentar de OS (fara namespace de retea, fara filesystem minimal "
            "read-only). Scriptul Luau tot nu are nicio cale spre io/os.execute/require "
            "(vezi host.cpp) - lipseste doar acest strat in plus pentru un eventual bug "
            "de memorie in interpretorul Luau insusi. Instaleaza bubblewrap (vezi "
            "astran_sandbox/Dockerfile) ca sa activezi acest strat."
        )
        return None

    try:
        probe = await asyncio.create_subprocess_exec(
            bwrap, "--unshare-all", "--die-with-parent",
            "--ro-bind", "/bin", "/bin",
            "--", "/bin/true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await asyncio.wait_for(probe.communicate(), timeout=5.0)
        if probe.returncode != 0:
            raise RuntimeError((err or b"").decode("utf-8", "replace")[:300] or f"exit code {probe.returncode}")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "bubblewrap e instalat dar nu functioneaza in acest mediu (%s) - probabil "
            "namespace-urile neprivilegiate sunt blocate aici. Sandbox-ul ruleaza fara "
            "jail-ul suplimentar de OS, la fel ca atunci cand bwrap lipseste.", exc,
        )
        return None

    _bwrap_path = bwrap
    log.info("bubblewrap activ: sandbox-ul Luau ruleaza izolat la nivel de OS (fara retea, filesystem read-only).")
    return bwrap


def _bwrap_wrap(bwrap: str, binary: str, prelude: Path, cmd: list[str]) -> list[str]:
    """Infasoara comanda luau-sandbox cu bubblewrap: fara retea si fara niciun alt
    namespace comun cu gazda (--unshare-all), filesystem READ-ONLY cu DOAR binarul si
    preludiul vizibile (nimic din restul serverului - .env, codul sursa, baza de date
    - nu exista in acest filesystem), /tmp propriu si gol, rulat ca "nobody"
    (uid/gid 65534), si omorat automat daca procesul Python-ului moare primul."""
    binary_abs = os.path.abspath(binary)
    prelude_abs = str(prelude.resolve())
    return [
        bwrap,
        "--ro-bind", binary_abs, binary_abs,
        "--ro-bind", prelude_abs, prelude_abs,
        "--tmpfs", "/tmp",
        "--proc", "/proc",
        "--dev", "/dev",
        "--chdir", "/",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--uid", "65534",
        "--gid", "65534",
        "--",
        *cmd,
    ]


# ---------- rezultat ----------

def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v):
        return None
    return float(v)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _bool(v: Any) -> Optional[bool]:
    return v if isinstance(v, bool) else None


def sanitize_ops(raw: Any) -> list[dict]:
    """Rezultatul vine dintr-un script necunoscut: valideaza tot inainte sa ajunga la clienti."""
    if not isinstance(raw, list):
        return []
    clean: list[dict] = []
    for op in raw[:MAX_OPS]:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        op_id = op.get("id")
        if kind not in OP_KINDS:
            continue
        if not isinstance(op_id, str) or not _ID_RE.match(op_id):
            continue
        t = _number(op.get("t"))
        item: dict = {"op": kind, "id": op_id, "t": _clamp(t if t is not None else 0.0, 0.0, 60.0)}

        if kind == "world":
            gravity = _number(op.get("gravity"))
            if gravity is not None:
                item["gravity"] = _clamp(gravity, 0.0, 200.0)
            air = _number(op.get("air"))
            if air is not None:
                item["air"] = _clamp(air, 0.0, 5.0)
            clean.append(item)
            continue

        if kind == "material":
            if not _MATERIAL_ID_RE.match(op_id):
                continue
            name = op.get("name")
            if isinstance(name, str):
                item["name"] = name[:64]
            density = _number(op.get("density"))
            if density is not None:
                item["density"] = _clamp(density, 0.001, 30.0)
            friction = _number(op.get("friction"))
            if friction is not None:
                item["friction"] = _clamp(friction, 0.0, 5.0)
            bounce = _number(op.get("bounce"))
            if bounce is not None:
                item["bounce"] = _clamp(bounce, 0.0, 1.2)
            liquid = _bool(op.get("liquid"))
            if liquid is not None:
                item["liquid"] = liquid
            color = op.get("color")
            if isinstance(color, str) and _HEX_COLOR.match(color):
                item["color"] = color.lower()
            clean.append(item)
            continue

        for axis in ("x", "y", "z"):
            n = _number(op.get(axis))
            if n is not None:
                item[axis] = _clamp(n, -10000.0, 10000.0)
        scale = _number(op.get("scale"))
        if scale is not None:
            item["scale"] = _clamp(scale, 0.05, 100.0)
        shape = op.get("type")
        if isinstance(shape, str) and shape in SHAPE_TYPES:
            item["type"] = shape
        color = op.get("color")
        if isinstance(color, str) and _HEX_COLOR.match(color):
            item["color"] = color.lower()
        name = op.get("name")
        if isinstance(name, str):
            item["name"] = name[:64]
        material = op.get("material")
        if isinstance(material, str) and (material == "" or _MATERIAL_ID_RE.match(material)):
            item["material"] = material
        for key in ("anchored", "collide"):
            b = _bool(op.get(key))
            if b is not None:
                item[key] = b
        for axis in ("vx", "vy", "vz"):
            n = _number(op.get(axis))
            if n is not None:
                item[axis] = _clamp(n, -200.0, 200.0)
        clean.append(item)
    clean.sort(key=lambda o: o["t"])  # sortare stabila: ordinea pastrata la timp egal
    return clean


def _empty_result(ok: bool = True, errors: Optional[list[str]] = None, truncated: bool = False) -> dict:
    return {
        "ok": ok,
        "output": [],
        "errors": errors or [],
        "ops": [],
        "duration": 0.0,
        "truncated": truncated,
    }


def parse_output(stdout: bytes, returncode: Optional[int]) -> dict:
    result = _empty_result()
    saw_ops = False

    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue

        kind = msg.get("type")
        if kind in ("print", "warn"):
            if len(result["output"]) < MAX_LOG_LINES:
                result["output"].append({"level": kind, "msg": str(msg.get("msg", ""))[:2000]})
        elif kind == "error":
            if len(result["errors"]) < 20:
                result["errors"].append(str(msg.get("msg", ""))[:2000])
        elif kind == "ops" and not saw_ops:
            saw_ops = True
            data = msg.get("data")
            if isinstance(data, dict):
                result["ops"] = sanitize_ops(data.get("ops"))
                duration = _number(data.get("duration"))
                result["duration"] = _clamp(duration if duration is not None else 0.0, 0.0, 60.0)
                result["truncated"] = bool(data.get("truncated"))

    if returncode not in (0, None) and not result["errors"]:
        result["errors"].append(f"Sandbox stopped unexpectedly (code {returncode})")

    result["ok"] = not result["errors"]
    return result


# ---------- rulare ----------

_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    return _semaphore


async def run_files(
    files: Any,
    timeout_ms: int = 2000,
    mem_mb: int = 64,
    assets: Optional[list[dict]] = None,
    scene: Any = None,
) -> dict:
    clean = validate_files(files)
    if not any(f["name"] == ENTRY_NAME for f in clean):
        raise ValueError(f"Missing the '{ENTRY_NAME}' script (it runs first)")

    binary = find_binary()
    if binary is None or not PRELUDE.is_file():
        raise SandboxUnavailable("luau-sandbox is not built on this server (run astran_sandbox/build.sh)")

    base_cmd = [
        binary,
        "--timeout", str(timeout_ms),
        "--mem", str(mem_mb),
        "--prelude", str(PRELUDE),
        "--entry", ENTRY_NAME,
        "-",
    ]
    bwrap = await _check_bwrap()
    cmd = _bwrap_wrap(bwrap, binary, PRELUDE, base_cmd) if bwrap else base_cmd

    async with _get_semaphore():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={},  # procesul copil nu vede secretele serverului (MONGO_URL, JWT_SECRET...)
            start_new_session=True,
            preexec_fn=_make_preexec(timeout_ms),
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(_bundle(clean, assets, scene)),
                timeout=timeout_ms / 1000 + 1.5,
            )
        except asyncio.TimeoutError:
            _kill(proc)
            await proc.wait()
            return _empty_result(ok=False, errors=["Script timed out"], truncated=True)

    if len(stdout) > MAX_STDOUT_BYTES:
        return _empty_result(ok=False, errors=["Script output too large"])

    return parse_output(stdout, proc.returncode)


async def run_script(source: str, timeout_ms: int = 2000, mem_mb: int = 64) -> dict:
    """Compatibilitate: un singur script = fisierul 'main'."""
    return await run_files(files_from_source(source), timeout_ms, mem_mb)


_cache: "OrderedDict[str, dict]" = OrderedDict()


async def run_cached_files(files: Any, assets: Optional[list[dict]] = None, scene: Any = None) -> dict:
    """Ca run_files, dar retine rezultatele recente (jocurile se ruleaza des, scriptul se schimba rar)."""
    clean = validate_files(files)
    key = _cache_key(clean, assets, scene)
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return hit

    result = await run_files(clean, assets=assets, scene=scene)
    if result["ok"]:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result