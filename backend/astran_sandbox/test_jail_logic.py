"""Teste de LOGICA pentru jail-ul bubblewrap - nu necesita bwrap/Luau instalate (folosesc un
bwrap si un luau-sandbox FALSE, scripturi shell). Rulare din folderul backend:

    python -m unittest astran_sandbox.test_jail_logic -v

Ele verifica politica din runner.py (fail-closed, comanda de jail, retry, env gol, rlimit),
NU izolarea reala a kernelului - pentru aceea vezi test_sandbox_security.py si
test_real_jail.py (necesita bwrap functional).
"""
from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from astran_sandbox import runner

# Scripturile false primesc directorul de test BATUT IN CONTINUT (@DIR@), fiindca runner.py
# porneste procesele cu env={} (exact ce vrem sa testam) - deci nu pot citi variabile de mediu.
FAKE_LUAU = r"""#!/bin/sh
touch "@DIR@/luau_ran"   # touch, nu >>: RLIMIT_FSIZE=0 interzice scrierea de date
echo '{"type":"print","msg":"astran-jail-ok"}'
echo "{\"type\":\"print\",\"msg\":\"ENV:[$(env | tr '\n' ' ')]\"}"
echo '{"type":"ops","data":{"ops":[],"duration":0,"truncated":false}}'
"""

# bwrap fals: numara apelurile; daca exista fisierul bwrap_broken esueaza ca in containere,
# altfel executa comanda de dupa `--` (ca un bwrap functional).
FAKE_BWRAP = r"""#!/bin/sh
touch "@DIR@/call.$$"
if [ -f "@DIR@/bwrap_broken" ]; then
  echo "bwrap: No permissions to creating new namespace, likely because the kernel does not allow non-privileged user namespaces." >&2
  exit 1
fi
while [ "$1" != "--" ]; do shift; done; shift
exec "$@"
"""


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body.replace("@DIR@", str(path.parent)))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class JailLogicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.env_backup = dict(os.environ)
        _write_exec(self.dir / "luau-sandbox", FAKE_LUAU)
        _write_exec(self.dir / "bwrap", FAKE_BWRAP)
        os.environ["LUAU_SANDBOX_BIN"] = str(self.dir / "luau-sandbox")
        os.environ["PATH"] = f"{self.dir}:{self.env_backup.get('PATH', '')}"
        os.environ["SUPER_SECRET_JWT"] = "do-not-leak"
        os.environ.pop("ASTRAN_SANDBOX_REQUIRE_BWRAP", None)
        runner._bwrap_path = None
        runner._probe_failure = ""
        runner._probe_failed_at = 0.0
        runner._probe_lock = None
        # binarul fals e un script shell, nu ELF: il tratam ca static (nu cere ldd)
        self._orig_static = runner._is_static_elf
        runner._is_static_elf = lambda p: True

    def tearDown(self):
        runner._is_static_elf = self._orig_static
        os.environ.clear()
        os.environ.update(self.env_backup)

    def _calls(self) -> int:
        return len(list(self.dir.glob("call.*")))

    async def test_working_jail_runs_script_without_server_secrets(self):
        r = await runner.run_files([{"name": "main", "source": "print(1)"}])
        self.assertTrue(r["ok"], r)
        env_line = next(l["msg"] for l in r["output"] if l["msg"].startswith("ENV:"))
        self.assertNotIn("do-not-leak", env_line)
        self.assertNotIn("SUPER_SECRET_JWT", env_line)

    async def test_broken_bwrap_fails_closed_and_script_never_runs(self):
        (self.dir / "bwrap_broken").write_text("1")
        with self.assertRaises(runner.SandboxUnavailable) as cm:
            await runner.run_files([{"name": "main", "source": "print(1)"}])
        msg = str(cm.exception)
        self.assertNotIn("REQUIRE_BWRAP", msg)          # nu indemnam utilizatorii sa dezactiveze jail-ul
        self.assertNotIn("namespace", msg.lower())       # fara detalii de server catre client
        self.assertFalse((self.dir / "luau_ran").exists(), "scriptul a rulat FARA jail!")
        self.assertIn("namespace", runner.sandbox_status()["last_failure"])  # motivul e pentru admin

    async def test_missing_bwrap_fails_closed(self):
        (self.dir / "bwrap").unlink()
        os.environ["PATH"] = "/nonexistent"
        with self.assertRaises(runner.SandboxUnavailable):
            await runner.run_files([{"name": "main", "source": "print(1)"}])
        self.assertFalse((self.dir / "luau_ran").exists())

    async def test_failure_is_retried_after_ttl_not_forever(self):
        (self.dir / "bwrap_broken").write_text("1")
        with self.assertRaises(runner.SandboxUnavailable):
            await runner.run_files([{"name": "main", "source": "print(1)"}])
        (self.dir / "bwrap_broken").unlink()                      # mediul "se repara"
        with self.assertRaises(runner.SandboxUnavailable):        # inca in fereastra de 30s: nu re-proba
            await runner.run_files([{"name": "main", "source": "print(1)"}])
        runner._probe_failed_at -= runner._BWRAP_RETRY_SECONDS + 1
        r = await runner.run_files([{"name": "main", "source": "print(1)"}])
        self.assertTrue(r["ok"], r)

    async def test_probe_runs_once_under_concurrency_and_success_is_cached(self):
        results = await asyncio.gather(*[runner._check_bwrap() for _ in range(20)])
        self.assertTrue(all(results))
        self.assertEqual(self._calls(), 1)
        await runner._check_bwrap()
        self.assertEqual(self._calls(), 1)

    async def test_concurrent_runs_are_independent_processes(self):
        rs = await asyncio.gather(*[
            runner.run_files([{"name": "main", "source": f"print({i})"}]) for i in range(8)])
        self.assertTrue(all(r["ok"] for r in rs))
        # 1 probe + 8 rulari, fiecare in propriul proces bwrap
        self.assertEqual(self._calls(), 9)

    async def test_require_bwrap_zero_is_the_only_opt_out_and_never_default(self):
        self.assertTrue(runner._require_bwrap())
        os.environ["ASTRAN_SANDBOX_REQUIRE_BWRAP"] = "0"
        self.assertFalse(runner._require_bwrap())


class JailCommandTests(unittest.TestCase):
    def _args(self, binary="/bin/true"):
        return runner._jail_args("/usr/bin/bwrap", binary, runner.PRELUDE, [binary, "-"])

    def test_isolation_flags_present(self):
        a = self._args()
        for flag in ("--unshare-all", "--unshare-user", "--die-with-parent", "--new-session",
                     "--clearenv", "--disable-userns"):
            self.assertIn(flag, a)
        self.assertEqual(a[a.index("--cap-drop") + 1], "ALL")
        self.assertEqual(a[a.index("--uid") + 1], "65534")
        self.assertNotIn("--share-net", a)

    def test_no_proc_or_dev_and_no_rw_binds(self):
        a = self._args()
        self.assertNotIn("--proc", a)
        self.assertNotIn("--dev", a)
        self.assertNotIn("--bind", a)
        self.assertNotIn("--bind-try", a)

    def test_only_binary_prelude_and_its_libs_are_visible(self):
        a = self._args("/bin/true")   # binar dinamic real
        binds = [a[i + 1] for i, x in enumerate(a) if x == "--ro-bind"]
        self.assertIn(os.path.abspath("/bin/true"), binds)
        self.assertIn(str(runner.PRELUDE.resolve()), binds)
        prelude = str(runner.PRELUDE.resolve())
        for p in binds:
            if p != prelude:
                self.assertFalse(p.startswith(("/etc", "/home", "/root", "/app", "/var", "/run")), p)
        self.assertTrue(any("libc.so" in p for p in binds), "libc ar trebui legat pentru binar dinamic")

    def test_static_detection(self):
        self.assertFalse(runner._is_static_elf("/bin/true"))     # dinamic
        self.assertFalse(runner._is_static_elf("/nonexistent"))


class RlimitTests(unittest.TestCase):
    def _nproc_in_child(self, jailed: bool) -> str:
        out = subprocess.run(
            [sys.executable, "-c", "import resource;print(resource.getrlimit(resource.RLIMIT_NPROC))"],
            capture_output=True, text=True, preexec_fn=runner._make_preexec(2000, jailed=jailed))
        return out.stdout.strip()

    def test_nproc_zero_only_when_not_jailed(self):
        self.assertEqual(self._nproc_in_child(False), "(0, 0)")
        self.assertNotEqual(self._nproc_in_child(True), "(0, 0)")


if __name__ == "__main__":
    unittest.main()