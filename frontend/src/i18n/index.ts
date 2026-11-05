import { getLocales } from "expo-localization";
import AsyncStorage from "@react-native-async-storage/async-storage";
import React from "react";

export type LangCode = "ro" | "en";

export const LANGUAGES: { code: LangCode; label: string; native: string }[] = [
  { code: "ro", label: "Romanian", native: "Română" },
  { code: "en", label: "English", native: "English" },
];

type Dict = Record<string, string>;

// Doar "ro" si "en" sunt incarcate. t() cade pe engleza pentru orice cheie lipsa.
const STRINGS: Partial<Record<LangCode, Dict>> = {
  ro: {
    app_name: "Astran Game",
    tagline: "Joacă. Creează. Explorează.",
    login: "Autentificare",
    register: "Înregistrare",
    logout: "Ieșire",
    email: "Email",
    password: "Parolă",
    username: "Nume utilizator",
    continue: "Continuă",
    or: "sau",
    google_login: "Continuă cu Google",
    age_select_title: "Alege categoria de vârstă",
    age_select_subtitle: "Poți schimba mai târziu din Setări",
    age_under_18: "Sub 18 ani",
    age_18_plus: "18+",
    age_under_desc: "Conținut sigur și moderare mai strictă",
    age_18_desc: "Acces la jocuri pentru adulți",
    discover: "Descoperă",
    studio: "Studio",
    friends: "Prieteni",
    profile: "Profil",
    for_you: "Pentru tine",
    trending: "În trend",
    new_games: "Noutăți",
    popular: "Populare",
    recently_played: "Recent jucate",
    play: "Joacă",
    astrans: "Astrans",
    wallet: "Portofel",
    buy_astrans: "Cumpără Astrans",
    transfer: "Transferă",
    transactions: "Tranzacții",
    settings: "Setări",
    language: "Limbă",
    graphics: "Grafică",
    view_distance: "Distanță vizualizare",
    create_game: "Creează joc",
    my_games: "Jocurile mele",
    search: "Caută",
    add_friend: "Adaugă prieten",
    accept: "Acceptă",
    reject: "Refuză",
    block: "Blochează",
    remove: "Șterge",
    online: "Online",
    offline: "Offline",
    incoming: "Primite",
    outgoing: "Trimise",
    reset: "Reset",
    save: "Salvează",
    cancel: "Anulează",
    welcome_bonus: "Bonus de bun venit",
    balance: "Sold",
    fee: "Comision",
    receive: "Primești",
    send: "Trimite",
    game_title: "Titlu joc",
    game_desc: "Descriere",
    public_game: "Joc public",
    game_created: "Joc creat!",
    error: "Eroare",
    loading: "Se încarcă...",
    privacy_prefix: "Am citit și accept",
    privacy_link: "Politica de Confidențialitate",
    chat: "Chat",
    chat_open: "Deschide chatul",
    chat_close: "Închide chatul",
    chat_placeholder: "Scrie un mesaj...",
    chat_send: "Trimite",
    chat_mode_filtered: "Chat filtrat",
    chat_mode_adult: "Chat 18+",
    chat_empty: "Niciun mesaj încă. Spune salut!",
    chat_offline: "Conexiune pierdută. Se reconectează...",
    chat_report: "Raportează",
    chat_reported: "Mesaj raportat. Mulțumim!",
    chat_block: "Blochează",
    chat_block_confirm: "Confirmă blocarea",
    chat_block_info: "Nu veți mai vedea mesajele acestui jucător.",
    chat_blocked_done: "Jucător blocat.",
    chat_retry: "Atinge pentru a retrimite",
    chat_sending: "Se trimite...",
    chat_err_empty: "Mesajul este gol.",
    chat_err_too_long: "Mesajul este prea lung.",
    chat_err_invalid: "Mesaj invalid.",
    chat_err_rate_limited: "Prea repede! Așteaptă puțin.",
    chat_err_duplicate: "Ai trimis deja acest mesaj.",
    chat_err_muted: "Nu poți scrie în chat acum.",
    chat_err_unavailable: "Chatul nu este disponibil. Încearcă din nou.",
    chat_err_offline: "Nu ești conectat la chat.",
    chat_err_timeout: "Mesajul nu a fost confirmat. Încearcă din nou.",
    chat_err_action: "Acțiunea a eșuat. Încearcă din nou.",
  },
  en: {
    app_name: "Astran Game", tagline: "Play. Create. Explore.",
    login: "Log in", register: "Sign up", logout: "Log out",
    email: "Email", password: "Password", username: "Username",
    continue: "Continue", or: "or", google_login: "Continue with Google",
    age_select_title: "Choose your age category",
    age_select_subtitle: "You can change this later in Settings",
    age_under_18: "Under 18", age_18_plus: "18+",
    age_under_desc: "Safer content and stricter moderation",
    age_18_desc: "Access to adult games",
    discover: "Discover", studio: "Studio", friends: "Friends", profile: "Profile",
    for_you: "For You", trending: "Trending", new_games: "New", popular: "Popular",
    recently_played: "Recently Played", play: "Play",
    astrans: "Astrans", wallet: "Wallet", buy_astrans: "Buy Astrans",
    transfer: "Transfer", transactions: "Transactions",
    settings: "Settings", language: "Language",
    graphics: "Graphics", view_distance: "View Distance",
    create_game: "Create Game", my_games: "My Games", search: "Search",
    add_friend: "Add Friend", accept: "Accept", reject: "Reject",
    block: "Block", remove: "Remove", online: "Online", offline: "Offline",
    incoming: "Incoming", outgoing: "Outgoing", reset: "Reset",
    save: "Save", cancel: "Cancel", welcome_bonus: "Welcome Bonus",
    balance: "Balance", fee: "Fee", receive: "Receives", send: "Send",
    game_title: "Game title", game_desc: "Description", public_game: "Public game",
    game_created: "Game created!", error: "Error", loading: "Loading...",
    privacy_prefix: "I have read and accept the",
    privacy_link: "Privacy Policy",
    chat: "Chat",
    chat_open: "Open chat",
    chat_close: "Close chat",
    chat_placeholder: "Write a message...",
    chat_send: "Send",
    chat_mode_filtered: "Filtered chat",
    chat_mode_adult: "18+ chat",
    chat_empty: "No messages yet. Say hi!",
    chat_offline: "Connection lost. Reconnecting...",
    chat_report: "Report",
    chat_reported: "Message reported. Thank you!",
    chat_block: "Block",
    chat_block_confirm: "Confirm block",
    chat_block_info: "You will no longer see this player's messages.",
    chat_blocked_done: "Player blocked.",
    chat_retry: "Tap to resend",
    chat_sending: "Sending...",
    chat_err_empty: "The message is empty.",
    chat_err_too_long: "The message is too long.",
    chat_err_invalid: "Invalid message.",
    chat_err_rate_limited: "Too fast! Please wait a moment.",
    chat_err_duplicate: "You already sent this message.",
    chat_err_muted: "You can't chat right now.",
    chat_err_unavailable: "Chat is unavailable. Please try again.",
    chat_err_offline: "You are not connected to chat.",
    chat_err_timeout: "The message was not confirmed. Please try again.",
    chat_err_action: "The action failed. Please try again.",
  },
};

// Doar romana si engleza sunt suportate: dispozitiv in romana -> "ro", orice altceva -> "en".
export function detectDeviceLang(): LangCode {
  try {
    const code = (getLocales()?.[0]?.languageCode || "").toLowerCase();
    if (code === "ro") return "ro";
  } catch {}
  return "en";
}

function isSupportedLang(l: unknown): l is LangCode {
  return l === "ro" || l === "en";
}

type Ctx = { lang: LangCode; setLang: (l: LangCode) => void; t: (k: keyof typeof STRINGS["en"]) => string };

const I18nContext = React.createContext<Ctx>({
  lang: "ro",
  setLang: () => {},
  t: (k) => STRINGS.ro?.[k as string] ?? String(k),
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = React.useState<LangCode>("ro");

  React.useEffect(() => {
    (async () => {
      const saved = await AsyncStorage.getItem("astran_lang");
      if (isSupportedLang(saved)) setLangState(saved);
      else setLangState(detectDeviceLang());
    })();
  }, []);

  const setLang = React.useCallback((l: LangCode) => {
    setLangState(l);
    AsyncStorage.setItem("astran_lang", l).catch(() => {});
  }, []);

  // Fallback pe 3 niveluri: limba curenta -> engleza -> cheia bruta (niciodata un ecran gol).
  const t = React.useCallback(
    (k: keyof typeof STRINGS["en"]) => STRINGS[lang]?.[k as string] ?? STRINGS.en?.[k as string] ?? String(k),
    [lang]
  );

  return React.createElement(I18nContext.Provider, { value: { lang, setLang, t } }, children);
}

export function useI18n() {
  return React.useContext(I18nContext);
}