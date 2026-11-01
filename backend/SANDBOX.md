# Sandbox Luau - cum functioneaza jail-ul OS

Scripturile creatorilor ruleaza in `luau-sandbox`, in interiorul unui jail `bubblewrap` (bwrap):
fara retea, fara PID/IPC/UTS/mount-uri ale gazdei, filesystem read-only cu DOAR binarul si
preludiul, uid 65534, fara capabilitati, env gol. Daca jail-ul nu porneste, scripturile sunt
REFUZATE (fail-closed). `ASTRAN_SANDBOX_REQUIRE_BWRAP=0` exista, dar NU se foloseste in productie.

## Diagnoza
    python -m astran_sandbox.diagnose        # din folderul backend, pe serverul real

## Docker
    docker build -t astran-backend --build-arg LUAU_TAG=<tag> .
    curl -fsSLo default.json https://raw.githubusercontent.com/moby/moby/master/profiles/seccomp/default.json
    python3 deploy/make_seccomp_profile.py default.json > deploy/astran-seccomp.json
    docker run --security-opt seccomp=deploy/astran-seccomp.json -p 8000:8000 --env-file .env astran-backend
    docker run --rm --security-opt seccomp=deploy/astran-seccomp.json astran-backend python -m astran_sandbox.diagnose

Ubuntu 24.04 ca gazda: daca `kernel.apparmor_restrict_unprivileged_userns=1`, containerul are nevoie si de
`--security-opt apparmor=unconfined` SAU de un profil AppArmor care permite `userns` pentru bwrap.

## Platforme fara control asupra seccomp/userns (PaaS)
Daca diagnoza arata ca user namespaces sunt blocate si nu poti schimba asta, jail-ul bwrap nu poate functiona
acolo. Optiuni sigure: un VPS/VM cu kernel propriu, Fly.io Machines, sau un runtime cu sandbox de kernel (gVisor).