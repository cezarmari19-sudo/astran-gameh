"""Diagnoza pas-cu-pas a jail-ului bubblewrap. Rulare (din folderul backend, PE SERVERUL real):

    python -m astran_sandbox.diagnose

Nu schimba nicio setare si NU dezactiveaza nimic: doar spune care strat blocheaza si ce
trebuie reparat (kernel, AppArmor, seccomp-ul containerului, bwrap lipsa, binar dinamic...).
Cod de iesire 0 = jail functional, 1 = nu.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from pathlib import Path

from astran_sandbox import runner


def _read(p: str) -> str | None:
    try:
        return Path(p).read_text().strip()
    except OSError:
        return None


def _hint(reason: str) -> str:
    r = reason.lower()
    table = [
        (r"no permissions to creating new namespace|operation not permitted.*(namespace|unshare|clone)|unshare.*not permitted",
         "Kernel/container interzice user namespaces neprivilegiate. In Docker: seccomp-ul implicit blocheaza "
         "unshare/clone(CLONE_NEWUSER) - ruleaza containerul cu profilul din deploy/ (make_seccomp_profile.py) sau "
         "cu --security-opt seccomp=<profil>. Pe PaaS fara acces la optiunile containerului (multe platforme) "
         "foloseste un host cu VM/kernel propriu (ex. VPS, Fly.io Machines) sau un runtime cu sandbox (gVisor)."),
        (r"setting up uid map|uid_map|permission denied.*map",
         "Scrierea uid_map a esuat: user namespaces dezactivate (user.max_user_namespaces=0) sau blocate de politica."),
        (r"apparmor|restrict_unprivileged_userns",
         "Ubuntu 23.10+/24.04: kernel.apparmor_restrict_unprivileged_userns=1 blocheaza bwrap fara profil AppArmor propriu. "
         "Instaleaza un profil pentru /usr/bin/bwrap (pachetul apparmor-profiles / profil 'bwrap-userns-restrict')."),
        (r"mount.*proc|can't mount proc",
         "Montarea /proc e blocata (containerul mascheaza /proc). Jail-ul curent nu mai monteaza /proc - daca vezi asta, "
         "ruleaza versiunea veche a runner.py."),
        (r"no such file or directory|execvp|exec.*failed",
         "Binarul din jail nu isi gaseste loader-ul/bibliotecile: reconstruieste-l static (bash astran_sandbox/build.sh)."),
        (r"creating new namespace|clone",
         "Crearea de namespace-uri a esuat (limite de procese/namespace-uri sau seccomp)."),
    ]
    for pat, text in table:
        if re.search(pat, r):
            return text
    return "Motiv necunoscut - citeste mesajul brut de mai sus."


async def main() -> int:
    ok = True

    def line(status: str, msg: str) -> None:
        print(f"[{status}] {msg}")

    binary = runner.find_binary()
    if binary is None:
        line("FAIL", "luau-sandbox nu e construit (bash astran_sandbox/build.sh)")
        return 1
    static = runner._is_static_elf(binary)
    line("ok" if static else "WARN", f"{binary}: {'static' if static else 'DINAMIC (jail-ul va lega bibliotecile prin ldd; recomandat: build static)'}")

    bwrap = shutil.which("bwrap")
    if bwrap is None:
        line("FAIL", "bwrap nu e instalat (apt-get install bubblewrap)")
        return 1
    line("ok", f"bwrap: {bwrap}")

    line("info", f"uid={os.getuid()} user.max_user_namespaces={_read('/proc/sys/user/max_user_namespaces')} "
                 f"unprivileged_userns_clone={_read('/proc/sys/kernel/unprivileged_userns_clone')} "
                 f"apparmor_restrict_unprivileged_userns={_read('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')}")
    status = _read("/proc/self/status") or ""
    sec = re.search(r"^Seccomp:\s*(\d)", status, re.M)
    line("info", f"Seccomp (procesul curent) = {sec.group(1) if sec else '?'} (2 = filtru activ, ex. profilul implicit Docker)")
    line("info", f"in container: {os.path.exists('/.dockerenv') or 'docker' in (_read('/proc/1/cgroup') or '')}")

    reason = await runner._probe_jail(bwrap, binary)
    if reason is None:
        line("ok", "jail real (aceeasi comanda ca la rulare) a executat un script Luau cu succes")
    else:
        ok = False
        line("FAIL", f"jail-ul nu porneste: {reason}")
        line("hint", _hint(reason))
    req = runner._require_bwrap()
    line("info", f"ASTRAN_SANDBOX_REQUIRE_BWRAP={'1 (fail-closed, recomandat)' if req else '0 (SCRIPTURILE RULEAZA FARA JAIL!)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))