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
    server_unreachable_title: "Serverul nu răspunde",
    server_unreachable_body: "Ești încă autentificat. Nu te-am deconectat — verifică conexiunea și încearcă din nou.",
    try_again: "Încearcă din nou",
    age_raise_blocked: "Categoria de vârstă nu mai poate fi ridicată la 18+. Contactează suportul.",
    login: "Autentificare",
    forgot_password: "Ai uitat parola?",
    forgot_title: "Resetare parolă",
    forgot_send_code: "Trimite codul",
    forgot_code: "Cod din email",
    forgot_new_password: "Parolă nouă (min. 6 caractere)",
    forgot_reset: "Resetează parola",
    forgot_sent: "Dacă există un cont cu email verificat, ți-am trimis un cod.",
    forgot_done: "Parola a fost schimbată. Autentifică-te cu parola nouă.",
    verify_email_title: "Verifică-ți emailul",
    verify_email_body: "Confirmă adresa de email ca să poți recupera contul și să primești bonusul de bun-venit.",
    verify_email_send: "Trimite codul",
    verify_email_code: "Cod de 6 cifre",
    verify_email_confirm: "Confirmă",
    verify_email_sent: "Cod trimis. Verifică emailul.",
    verify_email_done: "Email verificat.",
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
    messages: "Mesaje",
    dm_empty: "Nicio conversație încă. Apasă pe un prieten din lista de prieteni ca să începi.",
    dm_go_friends: "Mergi la prieteni",
    dm_you: "Tu",
    dm_yesterday: "Ieri",
    dm_placeholder: "Scrie un mesaj...",
    dm_empty_chat: "Niciun mesaj încă. Spune salut!",
    dm_new_messages: "Mesaje noi",
    dm_connecting: "Se conectează...",
    dm_not_friends: "Nu mai sunteți prieteni. Nu poți trimite mesaje noi.",
    dm_sending: "Se trimite...",
    dm_retry: "Atinge pentru a retrimite",
    dm_dismiss: "Atinge pentru a elimina",
    dm_report: "Raportează mesajul",
    dm_reported: "Mesaj raportat. Mulțumim!",
    dm_block: "Blochează prietenul",
    dm_block_title: "Blochezi acest prieten?",
    dm_block_body: "Nu veți mai putea schimba mesaje, iar conversația va dispărea din listă.",
    dm_block_confirm: "Blochează",
    dm_open_failed: "Nu am putut deschide conversația.",
    dm_load_failed: "Nu am putut încărca mesajele.",
    dm_err_empty: "Mesajul este gol.",
    dm_err_too_long: "Mesajul este prea lung (maxim 500 de caractere).",
    dm_err_invalid: "Mesaj invalid.",
    dm_err_rate_limited: "Prea repede! Așteaptă puțin.",
    dm_err_duplicate: "Ai trimis deja acest mesaj.",
    dm_err_muted: "Nu poți trimite mesaje acum.",
    dm_err_not_allowed: "Nu poți trimite mesaje acestui utilizator.",
    dm_err_unavailable: "Mesageria nu este disponibilă. Încearcă din nou.",
    dm_err_offline: "Fără conexiune la server.",
  },
  en: {
    app_name: "Astran Game", tagline: "Play. Create. Explore.",
    server_unreachable_title: "Server not responding",
    server_unreachable_body: "You are still signed in. We did not log you out — check your connection and try again.",
    try_again: "Try again",
    age_raise_blocked: "The age category can no longer be raised to 18+. Please contact support.",
    login: "Log in", register: "Sign up", logout: "Log out",
    forgot_password: "Forgot your password?", forgot_title: "Reset password", forgot_send_code: "Send code",
    forgot_code: "Code from email", forgot_new_password: "New password (min. 6 characters)", forgot_reset: "Reset password",
    forgot_sent: "If an account with a verified email exists, we sent you a code.",
    forgot_done: "Your password was changed. Log in with the new password.",
    verify_email_title: "Verify your email",
    verify_email_body: "Confirm your email address to be able to recover your account and receive the welcome bonus.",
    verify_email_send: "Send code", verify_email_code: "6-digit code", verify_email_confirm: "Confirm",
    verify_email_sent: "Code sent. Check your email.", verify_email_done: "Email verified.",
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
    messages: "Messages",
    dm_empty: "No conversations yet. Tap a friend in your friends list to start.",
    dm_go_friends: "Go to friends",
    dm_you: "You",
    dm_yesterday: "Yesterday",
    dm_placeholder: "Write a message...",
    dm_empty_chat: "No messages yet. Say hi!",
    dm_new_messages: "New messages",
    dm_connecting: "Connecting...",
    dm_not_friends: "You are no longer friends. You can't send new messages.",
    dm_sending: "Sending...",
    dm_retry: "Tap to retry",
    dm_dismiss: "Tap to dismiss",
    dm_report: "Report message",
    dm_reported: "Message reported. Thank you!",
    dm_block: "Block friend",
    dm_block_title: "Block this friend?",
    dm_block_body: "You will no longer be able to exchange messages and the conversation will disappear from your list.",
    dm_block_confirm: "Block",
    dm_open_failed: "Couldn't open the conversation.",
    dm_load_failed: "Couldn't load messages.",
    dm_err_empty: "The message is empty.",
    dm_err_too_long: "The message is too long (500 characters max).",
    dm_err_invalid: "Invalid message.",
    dm_err_rate_limited: "Too fast! Wait a moment.",
    dm_err_duplicate: "You already sent this message.",
    dm_err_muted: "You can't send messages right now.",
    dm_err_not_allowed: "You can't message this user.",
    dm_err_unavailable: "Messaging is unavailable. Please try again.",
    dm_err_offline: "No connection to the server.",
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