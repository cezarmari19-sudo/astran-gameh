// frontend/src/components/CodeEditor.tsx
// Editor de cod ADEVARAT (nu un TextInput colorat manual): ruleaza CodeMirror 6 intr-un
// WebView (react-native-webview e deja o dependinta a proiectului), incarcat din CDN.
// Ofera: indentare/tab-uri corecte, linii numerotate, syntax highlighting per limbaj
// (vezi projectTree.languageFor), undo/redo, search & replace (Ctrl/Cmd+F, Ctrl/Cmd+H),
// si autosave prin onChange (debounced) catre parinte.
//
// Comunicare RN <-> WebView, prin postMessage, mesaje JSON:
//   RN -> pagina: {type:"load", path, source, language}   - inlocuieste continutul, reseteaza undo
//                 {type:"find"}                            - deschide panoul de cautare CodeMirror
//   pagina -> RN: {type:"ready"}                            - editorul e initializat, poate primi "load"
//                 {type:"change", path, source}             - continut modificat (debounced ~150ms)
//                 {type:"error", message}                   - eroare ne-fatala (ex: un limbaj nu s-a incarcat)
//
// IMPORTANT despre `source`/`initialSource`: continutul "adevarat" cat timp un fisier e
// deschis traieste IN WebView (undo history, pozitia cursorului). Parintele primeste
// schimbari prin onChange si le tine doar ca sa stie ce sa salveze - NU trebuie sa
// re-trimita acel continut inapoi la editor, altfel s-ar reseta cursorul/undo la fiecare
// tasta. De-aia efectul de "load" de mai jos depinde DOAR de `path` (schimbarea de tab),
// nu de `initialSource`.
import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, Text, Pressable, StyleSheet, ActivityIndicator, Platform } from "react-native";
import { WebView } from "react-native-webview";
import { colors } from "@/src/theme";
import { languageFor, LangId } from "@/src/studio/projectTree";

type Props = {
  path: string;
  initialSource: string; // continutul de incarcat cand `path` se schimba (vezi nota de sus)
  onChange: (path: string, source: string) => void;
  findSignal?: number; // creste de fiecare data cand parintele vrea sa deschida cautarea
};

// language-loaders per LangId: fiecare incearca sa incarce modulul CM6 potrivit si cade
// silentios pe "fara highlighting" daca nu reuseste (extensie exotica, CDN indisponibil etc).
const LANG_IMPORTS: Record<LangId, string> = {
  lua: `
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { lua } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/lua");
    return StreamLanguage.define(lua);
  `,
  python: `
    const { python } = await import("https://esm.sh/@codemirror/lang-python@6");
    return python();
  `,
  javascript: `
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: false, typescript: false });
  `,
  jsx: `
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: true, typescript: false });
  `,
  typescript: `
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: false, typescript: true });
  `,
  tsx: `
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: true, typescript: true });
  `,
  cpp: `
    const { cpp } = await import("https://esm.sh/@codemirror/lang-cpp@6");
    return cpp();
  `,
  csharp: `
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { csharp } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/clike");
    return StreamLanguage.define(csharp);
  `,
  kotlin: `
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { kotlin } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/clike");
    return StreamLanguage.define(kotlin);
  `,
  json: `
    const { json } = await import("https://esm.sh/@codemirror/lang-json@6");
    return json();
  `,
  markdown: `
    const { markdown } = await import("https://esm.sh/@codemirror/lang-markdown@6");
    return markdown();
  `,
  html: `
    const { html } = await import("https://esm.sh/@codemirror/lang-html@6");
    return html();
  `,
  css: `
    const { css } = await import("https://esm.sh/@codemirror/lang-css@6");
    return css();
  `,
  yaml: `
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { yaml } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/yaml");
    return StreamLanguage.define(yaml);
  `,
  xml: `
    const { xml } = await import("https://esm.sh/@codemirror/lang-xml@6");
    return xml();
  `,
  plain: `return null;`,
};

// Pagina incarcata in WebView. Un singur fisier HTML self-contained care importa CodeMirror
// 6 ca module ESM direct din CDN (esm.sh face bundling on-the-fly, deci nu trebuie sa
// gestionam noi rezolvarea de dependinte). Temele/culorile sunt aliniate cu colors.ts.
function buildHtml(bg: string, fg: string, gutterBg: string, gutterFg: string, selectionBg: string, cursorColor: string): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<style>
  html, body { margin:0; padding:0; height:100%; background:${bg}; overflow:hidden; }
  #editor { height:100%; }
  .cm-editor { height:100%; font-size:13px; }
  .cm-scroller { font-family: ui-monospace, Menlo, Consolas, monospace; overflow:auto; }
  .cm-gutters { background:${gutterBg} !important; color:${gutterFg} !important; border-right: 1px solid rgba(255,255,255,0.08) !important; }
  .cm-activeLineGutter { background: rgba(255,255,255,0.06) !important; }
  .cm-activeLine { background: rgba(255,255,255,0.04) !important; }
  .cm-content { caret-color: ${cursorColor}; }
  .cm-selectionBackground { background: ${selectionBg} !important; }
</style>
</head>
<body>
<div id="editor"></div>
<script type="module">
  const post = (msg) => {
    if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(JSON.stringify(msg));
  };

  window.onerror = (msg) => post({ type: "error", message: String(msg) });
  // BUG REPARAT: o eroare intr-un "await import(...)" dintr-o functie async apare ca o
  // promisiune respinsa ("unhandled rejection"), NU ca eroare globala - window.onerror de mai
  // sus nu o prinde niciodata. Daca incarcarea modulelor CodeMirror esua (CDN blocat, firewall,
  // eroare de retea), intreg blocul de initializare pica silentios: nu se trimite nici "ready",
  // nici "error" - aplicatia ramanea cu un spinner etern, fara niciun indiciu despre ce s-a
  // intamplat. Handler-ul de mai jos prinde si acest caz si il raporteaza la fel ca o eroare.
  window.addEventListener("unhandledrejection", (e) => {
    const reason = e && e.reason;
    const message = (reason && (reason.message || reason.stack)) || String(reason);
    post({ type: "error", message: "unhandled rejection: " + message });
  });

  let view = null;
  let currentPath = null;
  let changeTimer = null;
  let suppressChange = false;
  const langCache = {};

  async function loadLanguage(langId) {
    if (langId in langCache) return langCache[langId];
    try {
      let ext = null;
      switch (langId) {
        case "lua": { 
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { lua } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/lua");
    return StreamLanguage.define(lua);
   }
        case "python": { 
    const { python } = await import("https://esm.sh/@codemirror/lang-python@6");
    return python();
   }
        case "javascript": { 
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: false, typescript: false });
   }
        case "jsx": { 
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: true, typescript: false });
   }
        case "typescript": { 
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: false, typescript: true });
   }
        case "tsx": { 
    const { javascript } = await import("https://esm.sh/@codemirror/lang-javascript@6");
    return javascript({ jsx: true, typescript: true });
   }
        case "cpp": { 
    const { cpp } = await import("https://esm.sh/@codemirror/lang-cpp@6");
    return cpp();
   }
        case "csharp": { 
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { csharp } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/clike");
    return StreamLanguage.define(csharp);
   }
        case "kotlin": { 
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { kotlin } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/clike");
    return StreamLanguage.define(kotlin);
   }
        case "json": { 
    const { json } = await import("https://esm.sh/@codemirror/lang-json@6");
    return json();
   }
        case "markdown": { 
    const { markdown } = await import("https://esm.sh/@codemirror/lang-markdown@6");
    return markdown();
   }
        case "html": { 
    const { html } = await import("https://esm.sh/@codemirror/lang-html@6");
    return html();
   }
        case "css": { 
    const { css } = await import("https://esm.sh/@codemirror/lang-css@6");
    return css();
   }
        case "yaml": { 
    const { StreamLanguage } = await import("https://esm.sh/@codemirror/language@6");
    const { yaml } = await import("https://esm.sh/@codemirror/legacy-modes@6/mode/yaml");
    return StreamLanguage.define(yaml);
   }
        case "xml": { 
    const { xml } = await import("https://esm.sh/@codemirror/lang-xml@6");
    return xml();
   }
        case "plain": { return null; }
      }
      langCache[langId] = ext;
      return ext;
    } catch (e) {
      post({ type: "error", message: "language '" + langId + "' failed to load: " + e });
      langCache[langId] = null;
      return null;
    }
  }

  (async () => {
    // BUG REPARAT: tot acest bloc rula fara try/catch. Daca oricare din importurile de mai
    // jos esua (CDN blocat/lent/indisponibil pe reteaua curenta), eroarea devenea o "unhandled
    // rejection" si intreg initul pica silentios - vezi handler-ul adaugat mai sus, care acum
    // o prinde; try/catch-ul de aici e un al doilea nivel de siguranta, pentru cazul (rar) in
    // care motorul WebView nu declanseaza deloc evenimentul "unhandledrejection".
    try {
      const { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection } =
        await import("https://esm.sh/@codemirror/view@6");
      const { EditorState, Compartment } = await import("https://esm.sh/@codemirror/state@6");
      const { defaultKeymap, history, historyKeymap, indentWithTab } = await import("https://esm.sh/@codemirror/commands@6");
      const { search, searchKeymap, openSearchPanel } = await import("https://esm.sh/@codemirror/search@6");
      const { indentOnInput, bracketMatching, syntaxHighlighting, defaultHighlightStyle, foldGutter, foldKeymap } =
        await import("https://esm.sh/@codemirror/language@6");
      const { closeBrackets, closeBracketsKeymap } = await import("https://esm.sh/@codemirror/autocomplete@6");
      const { oneDark } = await import("https://esm.sh/@codemirror/theme-one-dark@6");

      window.__cm = { EditorView, EditorState, Compartment };
      const langCompartment = new Compartment();
      window.__langCompartment = langCompartment;

      function onDocChanged(update) {
        if (!update.docChanged || suppressChange) return;
        clearTimeout(changeTimer);
        changeTimer = setTimeout(() => {
          post({ type: "change", path: currentPath, source: view.state.doc.toString() });
        }, 150);
      }

      const baseExtensions = [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        foldGutter(),
        search(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        oneDark,
        EditorView.lineWrapping,
        EditorState.tabSize.of(2),
        keymap.of([
          indentWithTab,
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
        ]),
        EditorView.updateListener.of(onDocChanged),
        langCompartment.of([]),
      ];
      window.__baseExtensions = baseExtensions;

      view = new EditorView({
        state: EditorState.create({ doc: "", extensions: baseExtensions }),
        parent: document.getElementById("editor"),
      });
      window.__view = view;

      post({ type: "ready" });
    } catch (e) {
      post({ type: "error", message: "editor init failed (check internet / CDN access): " + (e && (e.message || e.stack) || e) });
    }
  })();

  async function applyLoad(path, source, langId) {
    suppressChange = true;
    currentPath = path;
    const langExt = await loadLanguage(langId);
    const view = window.__view;
    const { EditorState } = window.__cm;
    view.setState(EditorState.create({ doc: source, extensions: window.__baseExtensions }));
    if (langExt) view.dispatch({ effects: window.__langCompartment.reconfigure(langExt) });
    suppressChange = false;
  }

  window.addEventListener("message", onMessage);
  document.addEventListener("message", onMessage); // Android RN WebView posteaza pe document, nu window

  function onMessage(e) {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "load") {
      applyLoad(msg.path, msg.source, msg.language);
    } else if (msg.type === "find") {
      if (window.__view) {
        import("https://esm.sh/@codemirror/search@6").then(({ openSearchPanel }) => {
          openSearchPanel(window.__view);
        });
      }
    }
  }
</script>
</body>
</html>`;
}

export default function CodeEditor({ path, initialSource, onChange, findSignal }: Props) {
  const webRef = useRef<WebView>(null);
  const [ready, setReady] = useState(false);
  // BUG REPARAT: inainte, "ready" ramanea pur si simplu false la nesfarsit daca init-ul din
  // WebView esua (CDN blocat/indisponibil) - un spinner etern, fara niciun indiciu. Acum:
  // - `loadError` retine mesajul de eroare primit din WebView (sau unul de timeout), ca sa-l
  //   aratam efectiv pe ecran, nu doar in console.log (unde userul de pe telefon nu-l vede).
  // - dupa TIMEOUT_MS fara "ready", consideram initul esuat chiar daca WebView-ul n-a raportat
  //   explicit nicio eroare (ex: un "await import" care ramane blocat la nesfarsit pe retea).
  // - `retryKey` forteaza un remount complet al WebView-ului la apasarea "Incearca din nou".
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const readyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const TIMEOUT_MS = 15000;
  // Tinem ultimul path pentru care am cerut "load", ca sa nu trimitem load-uri duplicate
  // daca `initialSource` se schimba fara ca `path`-ul sa se schimbe (vezi nota din header).
  const loadedPathRef = useRef<string | null>(null);
  const pendingLoadRef = useRef<{ path: string; source: string } | null>(null);

  useEffect(() => {
    readyTimeoutRef.current = setTimeout(() => {
      setLoadError(prev => prev ?? "Editorul nu a pornit in timp util. Verifica accesul la internet (foloseste esm.sh pentru a incarca CodeMirror) si incearca din nou.");
    }, TIMEOUT_MS);
    return () => { if (readyTimeoutRef.current) clearTimeout(readyTimeoutRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryKey]);

  function retry() {
    setLoadError(null);
    setReady(false);
    loadedPathRef.current = null;
    pendingLoadRef.current = null;
    setRetryKey(k => k + 1);
  }

  const html = useRef(
    buildHtml(colors.surface2, colors.onSurface, colors.surface3, colors.onSurface3, "rgba(204,255,0,0.25)", colors.brand)
  ).current;

  const sendLoad = useCallback((p: string, source: string) => {
    if (!ready || !webRef.current) {
      pendingLoadRef.current = { path: p, source };
      return;
    }
    loadedPathRef.current = p;
    webRef.current.postMessage(JSON.stringify({ type: "load", path: p, source, language: languageFor(p) }));
  }, [ready]);

  // Se declanseaza DOAR la schimbarea de path (deschiderea unui alt tab), niciodata la
  // fiecare tasta - vezi explicatia din header despre `initialSource`.
  useEffect(() => {
    if (loadedPathRef.current === path) return;
    sendLoad(path, initialSource);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ready]);

  useEffect(() => {
    if (!findSignal || !ready || !webRef.current) return;
    webRef.current.postMessage(JSON.stringify({ type: "find" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [findSignal]);

  const onWebMessage = useCallback((e: any) => {
    let msg: any;
    try { msg = JSON.parse(e.nativeEvent.data); } catch { return; }
    if (msg.type === "ready") {
      if (readyTimeoutRef.current) clearTimeout(readyTimeoutRef.current);
      setLoadError(null);
      setReady(true);
      const pending = pendingLoadRef.current;
      pendingLoadRef.current = null;
      if (pending) sendLoad(pending.path, pending.source);
      else sendLoad(path, initialSource);
    } else if (msg.type === "change") {
      onChange(msg.path, msg.source);
    } else if (msg.type === "error") {
      // BUG REPARAT: mesajul de eroare venea doar in console.log, invizibil pentru userul de
      // pe telefon. O eroare aici (de obicei un "await import" esuat - vezi CDN-ul esm.sh din
      // pagina de mai sus) inseamna de obicei ca editorul n-a pornit deloc, deci o aratam ca
      // stare de eroare pe ecran, cu buton de reincercare, in loc sa lasam spinner-ul la infinit.
      console.log("[CodeEditor]", msg.message);
      if (!ready) setLoadError(String(msg.message || "Eroare necunoscuta la initializarea editorului."));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onChange, ready]);

  return (
    <View style={styles.wrap}>
      <WebView
        key={retryKey}
        ref={webRef}
        testID="code-editor-webview"
        source={{ html }}
        originWhitelist={["*"]}
        onMessage={onWebMessage}
        javaScriptEnabled
        domStorageEnabled
        style={styles.webview}
        // pe Android, mesajele RN->WebView ajung mai fiabil pe "message" pe `document`
        // (vezi listener-ul dublu din pagina); nimic de configurat suplimentar aici
      />
      {!ready && !loadError ? (
        <View style={styles.loading} pointerEvents="none">
          <ActivityIndicator color={colors.brand} />
        </View>
      ) : null}
      {loadError ? (
        <View style={styles.loading} testID="code-editor-error">
          <Text style={styles.errorText}>{loadError}</Text>
          <Pressable testID="code-editor-retry" onPress={retry} style={styles.retryBtn}>
            <Text style={styles.retryBtnText}>Încearcă din nou</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: colors.surface2 },
  webview: { flex: 1, backgroundColor: colors.surface2 },
  loading: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface2, padding: 24, gap: 14 },
  errorText: { color: colors.onSurface2, fontSize: 13, textAlign: "center", lineHeight: 19 },
  retryBtn: { backgroundColor: colors.brand, paddingHorizontal: 18, paddingVertical: 10, borderRadius: 10 },
  retryBtnText: { color: colors.onBrand ?? "#0F1012", fontWeight: "700", fontSize: 13 },
});