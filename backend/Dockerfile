# Backend Astran + sandbox Luau cu jail bubblewrap.
#   docker build -t astran-backend --build-arg LUAU_TAG=<tag-fix-luau> .      (din folderul backend)
# Rulare: vezi SANDBOX.md (containerul are nevoie de un seccomp care permite user namespaces,
# altfel bwrap nu porneste si scripturile sunt REFUZATE - fail-closed).

# ---- 1) compileaza luau-sandbox STATIC (jail-ul nu are biblioteci de sistem) ----
FROM python:3.12-slim-bookworm AS luau-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake make g++ ca-certificates && rm -rf /var/lib/apt/lists/*
# Recomandat: fixeaza o versiune (build reproductibil). Gol = ultima de pe main.
ARG LUAU_TAG=""
ENV LUAU_TAG=${LUAU_TAG}
WORKDIR /build
COPY astran_sandbox ./astran_sandbox
RUN bash astran_sandbox/build.sh \
 && file astran_sandbox/bin/luau-sandbox | grep -q "statically linked"

# ---- 2) imaginea de rulare ----
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap \
 && rm -rf /var/lib/apt/lists/*
# Utilizator neprivilegiat: bwrap ruleaza fara setuid, prin user namespaces.
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=luau-build /build/astran_sandbox/bin/luau-sandbox /app/astran_sandbox/bin/luau-sandbox
RUN chown -R app:app /app
USER app
ENV ASTRAN_SANDBOX_REQUIRE_BWRAP=1
# Esueaza build-ul/pornirea containerului daca jail-ul nu merge: `docker run ... python -m astran_sandbox.diagnose`
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]