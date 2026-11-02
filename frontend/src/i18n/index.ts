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