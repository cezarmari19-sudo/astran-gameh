"""Teste de izolare REALA, prin bwrap adevarat: un `sh` joaca rolul codului nativ ostil care a
scapat din interpretorul Luau (Luau singur nu poate face nimic din asta). Se SAR automat daca
bwrap nu functioneaza in mediu - rulati-le pe serverul de productie / in imaginea Docker:

    python -m pytest astran_sandbox/test_real_jail.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

from astran_sandbox import runner

BWRAP = shutil.which("bwrap")


def _jail(script: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    cmd = runner._jail_args(BWRAP, "/bin/sh", runner.PRELUDE, ["/bin/sh", "-c", script])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env_extra or {})


@pytest.fixture(scope="module", autouse=True)
def _need_working_bwrap():
    if BWRAP is None:
        pytest.skip("bwrap not installed")
    try:
        r = _jail("echo ok")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"bwrap not usable here: {exc}")
    if r.returncode != 0 or "ok" not in r.stdout:
        pytest.skip(f"bwrap not usable here: {r.stderr.strip()[:200]}")


def test_host_files_not_visible():
    r = _jail("test -e /etc/passwd && echo VISIBLE; test -e /home && echo VISIBLE; test -e /app && echo VISIBLE; echo done")
    assert "VISIBLE" not in r.stdout and "done" in r.stdout


def test_secret_env_not_visible():
    os.environ["SUPER_SECRET_JWT"] = "do-not-leak"
    try:
        r = _jail("set", env_extra=dict(os.environ))   # chiar daca procesul parinte ARE secretul
    finally:
        os.environ.pop("SUPER_SECRET_JWT", None)
    assert "do-not-leak" not in r.stdout


def test_filesystem_is_read_only():
    marker = tempfile.mktemp()
    r = _jail(f"echo x > /bin/sh 2>/dev/null && echo WROTE; echo x > {marker} 2>/dev/null && echo WROTE_HOST; echo done")
    assert "WROTE" not in r.stdout and not os.path.exists(marker)


def test_no_network():
    # fara interfete de retea: nicio conexiune nu poate iesi (nu exista nici loopback configurat)
    r = _jail("exec 3<>/dev/tcp/127.0.0.1/80 2>/dev/null && echo CONNECTED; echo done")
    assert "CONNECTED" not in r.stdout