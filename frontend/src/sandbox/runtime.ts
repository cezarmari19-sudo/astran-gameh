export function buildRunMessage(opts: SandboxRunOptions) {
  const clean = validateFiles(opts.files);
  if (!clean.some((f) => f.name === ENTRY_NAME)) {
    throw new Error(`Missing the '${ENTRY_NAME}' script (it runs first)`);
  }
  const timeoutMs = opts.timeoutMs ?? 2000;
  const caps = (opts.capabilities ?? []).join(",");
  return {
    type: "run" as const,
    prelude: opts.preludeSource,
    bundle: buildBundle(clean, opts.assets, opts.scene),
    timeoutMs,
    memMB: opts.memMB ?? 64,
    caps,
    entry: ENTRY_NAME,
  };
}