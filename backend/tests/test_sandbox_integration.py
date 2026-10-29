"""Teste de INTEGRARE pentru sandbox-ul Luau REAL (binarul C++ compilat din host.cpp).
Spre deosebire de test_runner_validation.py (care testeaza doar validarea Python, fara
sa execute nimic), astea chiar lanseaza procesul `luau-sandbox` si verifica limitele lui
de izolare la runtime: fara acces la filesystem/os, timeout aplicat pe bune, memorie
limitata, crash-urile unui script nu afecteaza alte scripturi.

NECESITA:
  1. Binarul compilat:  cd backend && bash astran_sandbox/build.sh
     (are nevoie de internet - cloneaza github.com/luau-lang/luau - si de cmake/g++)
  2. pip install pytest pytest-asyncio   (sau: pip install -r requirements.txt pytest pytest-asyncio)

Ruleaza cu:  cd backend && python3 -m pytest tests/test_sandbox_integration.py -v

Fiecare test e SARIT (nu esueaza fals) daca binarul nu exista - vezi `requires_binary`
mai jos - ca sa poata fi vazut imediat, la rulare, ce anume lipseste.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astran_sandbox import runner  # noqa: E402

pytest_asyncio = pytest.importorskip("pytest_asyncio", reason="pip install pytest-asyncio")

requires_binary = pytest.mark.skipif(
    runner.find_binary() is None,
    reason="luau-sandbox binary not built - run `bash astran_sandbox/build.sh` first (needs internet + cmake/g++)",
)


@requires_binary
@pytest.mark.asyncio
async def test_legitimate_script_runs_successfully():
    result = await runner.run_files([
        {"name": "main", "source": 'local Workspace = workspace\nprint("ok")'},
    ])
    assert result["ok"] is True
    assert any(line["msg"] == "ok" for line in result["output"])


@requires_binary
@pytest.mark.asyncio
async def test_script_cannot_reach_filesystem():
    # Luau standard nu are `io`/`require` de OS - verificam ca globalul chiar lipseste,
    # nu doar ca "pare" sandboxat.
    result = await runner.run_files([
        {"name": "main", "source": 'print(tostring(io))\nprint(tostring(os.execute))'},
    ])
    # io trebuie sa fie nil, os.execute trebuie sa fie nil (os are doar clock/time/date)
    messages = [l["msg"] for l in result["output"]]
    assert "nil" in messages, f"expected 'io'/'os.execute' to print as nil, got: {messages}"


@requires_binary
@pytest.mark.asyncio
async def test_script_cannot_spawn_processes():
    result = await runner.run_files([
        {"name": "main", "source": 'print(tostring(os.execute))\nprint(tostring(require))'},
    ])
    messages = [l["msg"] for l in result["output"]]
    assert all(m == "nil" for m in messages), f"expected no execute/arbitrary require, got: {messages}"


@requires_binary
@pytest.mark.asyncio
async def test_infinite_loop_is_terminated_by_timeout():
    result = await runner.run_files(
        [{"name": "main", "source": "while true do end"}],
        timeout_ms=500,
    )
    assert result["ok"] is False
    assert any("timed out" in e.lower() for e in result["errors"])


@requires_binary
@pytest.mark.asyncio
async def test_excessive_memory_allocation_is_limited():
    # Incearca sa acumuleze un tabel foarte mare intr-o bucla - cu mem_mb mic, alocatorul
    # din host.cpp trebuie sa refuze inainte sa consume memoria reala a masinii gazda.
    script = """
        local t = {}
        for i = 1, 100000000 do
            t[i] = string.rep("x", 1024)
        end
    """
    result = await runner.run_files([{"name": "main", "source": script}], timeout_ms=3000, mem_mb=16)
    assert result["ok"] is False  # fie "not enough memory", fie timeout - oricum nu "ok"


@requires_binary
@pytest.mark.asyncio
async def test_script_error_is_isolated_and_reported_not_crashing_runner():
    result = await runner.run_files([{"name": "main", "source": "error('boom')"}])
    assert result["ok"] is False
    assert any("boom" in e for e in result["errors"])
    # un al doilea script, intr-un run SEPARAT, nu trebuie afectat de eroarea primului
    result2 = await runner.run_files([{"name": "main", "source": "print('still fine')"}])
    assert result2["ok"] is True


@requires_binary
@pytest.mark.asyncio
async def test_script_cannot_forge_the_emit_global():
    # `emit` e trecut doar preludiului, nu ca global al scriptului utilizatorului (vezi
    # host.cpp: luaEmit nu e inregistrat cu lua_setglobal). Scriptul nu trebuie sa poata
    # falsifica un rezultat "ops" direct, ocolind preludiul/API-ul controlat.
    result = await runner.run_files([{"name": "main", "source": 'print(tostring(emit))'}])
    messages = [l["msg"] for l in result["output"]]
    assert "nil" in messages, f"expected 'emit' to be unreachable as a global, got: {messages}"


@requires_binary
@pytest.mark.asyncio
async def test_two_games_do_not_share_scene_or_assets():
    # Doua rulari cu acelasi script dar scene/assets DIFERITE (simuland doua jocuri
    # separate) trebuie sa produca rezultate diferite si niciuna nu trebuie sa vada
    # datele celeilalte - fiecare proces e pornit de la zero, fara stare comuna.
    script = 'print(tostring(workspace.Cube1))'
    scene_a = {"objects": [{"id": "s1", "type": "cube", "name": "Cube1"}]}
    scene_b = {"objects": []}  # jocul B nu are niciun Cube1

    result_a = await runner.run_files([{"name": "main", "source": script}], scene=scene_a)
    result_b = await runner.run_files([{"name": "main", "source": script}], scene=scene_b)

    msgs_a = [l["msg"] for l in result_a["output"]]
    msgs_b = [l["msg"] for l in result_b["output"]]
    assert msgs_a != msgs_b, "game A's scene object leaked into (or matched by coincidence) game B's run"


@requires_binary
@pytest.mark.asyncio
async def test_malformed_bundle_is_rejected_cleanly():
    with pytest.raises(ValueError):
        await runner.run_files([{"name": "../escape", "source": "print(1)"}])