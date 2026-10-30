"""Teste de securitate REALE pentru luau-sandbox - incearca sa compromita sandbox-ul,
nu doar verifica ca apare un mesaj de eroare "frumos".

Rulare (dupa `bash astran_sandbox/build.sh`, din folderul `backend`):

    pytest astran_sandbox/test_sandbox_security.py -v

Daca `bwrap` nu functioneaza in mediul tau (multe containere CI blocheaza namespace-uri
neprivilegiate), seteaza inainte:

    ASTRAN_SANDBOX_REQUIRE_BWRAP=0 pytest astran_sandbox/test_sandbox_security.py -v

- asta ruleaza testele doar cu stratul rlimit+env-gol, nu cu jail-ul suplimentar de OS.
Un test e considerat REUSIT doar daca scriptul e blocat (eroare / truncat / killed),
niciodata doar pentru ca "nu a picat procesul".

ACOPERIRE vs cele 14 teste cerute: #3 (memoria altor procese), #11 (bridge frontend-backend)
si #14 (date ale altor utilizatori) NU sunt testabile la acest nivel - sunt acoperite (sau
nu inca, vezi raportul) de alte straturi (izolare de proces per rulare / game_permissions.py
/ runtime client, care inca nu exista). Sunt marcate explicit `skip`, nu simulate.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from astran_sandbox import runner


def _run(files, **kw):
    return asyncio.run(runner.run_files(files, **kw))


def _main(src: str) -> list[dict]:
    return [{"name": "main", "source": src}]


@pytest.fixture(autouse=True, scope="module")
def _require_binary():
    if runner.find_binary() is None:
        pytest.skip("luau-sandbox binary not built - run astran_sandbox/build.sh first")


# 1/2/4 - fara io, fara os.execute, fara acces la fisiere/date private ------------------

def test_no_file_access():
    r = _run(_main('local ok, e = pcall(function() return io.open("/etc/passwd") end) '
                    'if ok then error("io was reachable") end'))
    assert r["ok"], r["errors"]


def test_no_os_execute():
    r = _run(_main('local ok, e = pcall(function() return os.execute("id") end) '
                    'if ok then error("os.execute was reachable") end'))
    assert r["ok"], r["errors"]


def test_os_library_is_clock_only():
    # os trebuie sa aiba doar clock/time/date - orice altceva trebuie sa fie nil
    r = _run(_main(
        'local dangerous = {"getenv","remove","rename","tmpname","execute"} '
        'for _, name in ipairs(dangerous) do '
        '  if os[name] ~= nil then error("os." .. name .. " should not exist") end '
        'end'
    ))
    assert r["ok"], r["errors"]


# 5 - API neautorizate: Assets fara capability -------------------------------------------

def test_assets_blocked_without_capability():
    # fara `assets=` la run_files(), host-ul nu primeste --caps assets -> prelude nu
    # seteaza global Assets deloc.
    r = _run(_main('local ok, e = pcall(function() return Assets.load("x") end) '
                    'if ok then error("Assets was reachable without the capability") end'))
    assert r["ok"], r["errors"]


def test_assets_available_with_capability():
    # cu `assets=` populat, capacitatea e acordata si Assets.load functioneaza normal
    # (ramane modulul existent de validare a datelor - testam doar ca APIul exista).
    assets = [{"id": "a1", "name": "Box", "object": {"type": "cube", "color": "#ff0000", "scale": 1}}]
    r = _run(_main('local ok = pcall(function() return Assets.load("a1") end) '
                    'if not ok then error("Assets.load should work when capability is granted") end'),
             assets=assets)
    assert r["ok"], r["errors"]


# 7 - modificarea permisiunilor sandbox-ului (_CAPS e read-only) -------------------------

def test_caps_table_is_readonly():
    r = _run(_main('local ok = pcall(function() _CAPS.assets = true end) '
                    'if ok then error("_CAPS was writable") end'))
    assert r["ok"], r["errors"]


def test_caps_cannot_be_replaced():
    r = _run(_main('local ok = pcall(function() _CAPS = {assets = true} end) '
                    'if ok and _CAPS.assets then error("_CAPS global was reassignable") end'))
    assert r["ok"], r["errors"]


# 8 - incercarea de a "dezactiva" sandbox-ul prin rescrierea globalelor host-ului --------

def test_cannot_override_print():
    r = _run(_main('local ok = pcall(function() print = function() end end) '
                    'if ok then error("print was overridable") end'))
    assert r["ok"], r["errors"]


# 9 - escaladare / path traversal prin require -------------------------------------------

def test_require_path_traversal_fails_safely():
    r = _run(_main('local ok = pcall(function() return require("../../../etc/passwd") end) '
                    'if ok then error("traversal require unexpectedly succeeded") end'))
    assert r["ok"], r["errors"]


def test_invalid_file_name_rejected_before_running():
    with pytest.raises(ValueError):
        runner.validate_files([{"name": "../evil", "source": "print(1)"}])


# 12 - memorie in exces ---------------------------------------------------------------

def test_memory_bomb_is_stopped():
    r = _run(_main(
        'local t = {} '
        'while true do '
        '  local chunk = string.rep("x", 1024 * 1024) '
        '  table.insert(t, chunk) '
        'end'
    ), mem_mb=16)
    assert not r["ok"]


# 13 - bucla infinita / blocarea mediului de executie ------------------------------------

def test_infinite_loop_times_out():
    r = _run(_main('while true do end'), timeout_ms=500)
    assert not r["ok"] or r["truncated"]
    assert "timed out" in " ".join(r["errors"]).lower() or r["truncated"]


# 6 - fara scurgere de stare intre rulari (fiecare rulare = proces nou) -------------------

def test_no_state_leak_between_runs():
    r1 = _run(_main('_G.leaked = "secret-from-run-1"; print("set")'))
    assert r1["ok"]
    r2 = _run(_main(
        'if _G.leaked ~= nil then error("state leaked from a previous run: " .. tostring(_G.leaked)) end '
        'print("clean")'
    ))
    assert r2["ok"], r2["errors"]


# ---- in afara acestui test file: N/A la acest nivel, nu simulate -----------------------

@pytest.mark.skip(reason=(
    "#3 memoria altor PROCESE: nu e testabil din interiorul Luau (nicio API de FFI/pointere "
    "e expusa catre script). Izolarea reala e asigurata de faptul ca fiecare rulare e un "
    "proces de SO separat + bwrap (namespace-uri separate) - verifica manual pe infra ta ca "
    "bwrap chiar functioneaza (vezi avertismentele din log la pornire)."
))
def test_other_process_memory_not_reachable():
    ...


@pytest.mark.skip(reason=(
    "#11 bridge frontend-backend: nu exista inca runtime client (vezi routes.py - fisierele "
    "target=\"client\" sunt salvate dar NU executate nicaieri azi). Nimic de testat pana nu "
    "exista implementarea reala - a scrie un test acum ar fi un test fictiv."
))
def test_frontend_backend_bridge():
    ...


@pytest.mark.skip(reason=(
    "#14 date ale altor utilizatori: nu e o problema a VM-ului Luau, ci a stratului de "
    "permisiuni al aplicatiei (game_permissions.py / rutele Mongo). Are nevoie de un audit "
    "separat, cu teste la nivel de API HTTP, nu la nivel de sandbox de scripting."
))
def test_other_users_data():
    ...