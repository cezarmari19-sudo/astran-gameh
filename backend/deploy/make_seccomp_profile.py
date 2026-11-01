#!/usr/bin/env python3
"""Genereaza un profil seccomp Docker = profilul implicit al Docker + DOAR apelurile de care
bubblewrap are nevoie ca sa creeze user namespaces. Mult mai putin permisiv decat
`seccomp=unconfined`: restul filtrului implicit ramane.

    curl -fsSLo default.json https://raw.githubusercontent.com/moby/moby/master/profiles/seccomp/default.json
    python3 deploy/make_seccomp_profile.py default.json > deploy/astran-seccomp.json
    docker run --security-opt seccomp=deploy/astran-seccomp.json ...

NOTA: apelurile se permit la nivel de container; izolarea scripturilor o face apoi bwrap
(user/pid/net/ipc/uts/cgroup namespace + fs read-only + --disable-userns).
"""
import json
import sys

NEEDED = ["unshare", "setns", "clone", "clone3", "mount", "umount2", "pivot_root",
          "sethostname", "setdomainname"]

prof = json.load(open(sys.argv[1]))
prof.setdefault("syscalls", []).append({
    "names": NEEDED,
    "action": "SCMP_ACT_ALLOW",
    "comment": "bubblewrap: user/pid/net/mount namespaces for the Luau sandbox jail",
})
json.dump(prof, sys.stdout, indent=2)