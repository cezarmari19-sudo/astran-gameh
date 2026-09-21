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
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")


class SandboxUnavailable(RuntimeError):
    """Binarul luau-sandbox nu este instalat pe server."""


# ---------- fisiere ----------

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


def _bundle(files: list[dict]) -> bytes:
    parts: list[bytes] = []
    for f in files:
        data = f["source"].encode("utf-8")
        parts.append(b"@@FILE " + f["name"].encode("ascii") + b" " + str(len(data)).encode("ascii") + b"\n")
        parts.append(data)
        parts.append(b"\n")
    return b"".join(parts)


def _cache_key(files: list[dict]) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f["name"].encode("utf-8"))
        h.update(b"\0")
        data = f["source"].encode("utf-8")
        h.update(str(len(data)).encode("ascii"))
        h.update(b"\0")
        h.update(data)
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


# ---------- rezultat ----------

def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v):
        return None
    return float(v)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


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
        if kind not in ("create", "set", "destroy"):
            continue
        if not isinstance(op_id, str) or not (0 < len(op_id) <= 16):
            continue
        t = _number(op.get("t"))
        item: dict = {"op": kind, "id": op_id, "t": _clamp(t if t is not None else 0.0, 0.0, 60.0)}
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


async def run_files(files: Any, timeout_ms: int = 2000, mem_mb: int = 64) -> dict:
    clean = validate_files(files)
    if not any(f["name"] == ENTRY_NAME for f in clean):
        raise ValueError(f"Missing the '{ENTRY_NAME}' script (it runs first)")

    binary = find_binary()
    if binary is None or not PRELUDE.is_file():
        raise SandboxUnavailable("luau-sandbox is not built on this server (run astran_sandbox/build.sh)")

    async with _get_semaphore():
        proc = await asyncio.create_subprocess_exec(
            binary,
            "--timeout", str(timeout_ms),
            "--mem", str(mem_mb),
            "--prelude", str(PRELUDE),
            "--entry", ENTRY_NAME,
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={},  # procesul copil nu vede secretele serverului (MONGO_URL, JWT_SECRET...)
            start_new_session=True,
            preexec_fn=_make_preexec(timeout_ms),
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(_bundle(clean)),
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


async def run_cached_files(files: Any) -> dict:
    """Ca run_files, dar retine rezultatele recente (jocurile se ruleaza des, scriptul se schimba rar)."""
    clean = validate_files(files)
    key = _cache_key(clean)
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return hit

    result = await run_files(clean)
    if result["ok"]:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result