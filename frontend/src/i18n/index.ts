import { getLocales } from "expo-localization";
import AsyncStorage from "@react-native-async-storage/async-storage";
import React from "react";

export type LangCode =
  | "ro" | "en" | "es" | "ru" | "de" | "fr" | "it" | "pt" | "ar" | "zh" | "ja" | "hi";

export const LANGUAGES: { code: LangCode; label: string; native: string }[] = [
  { code: "ro", label: "Romanian", native: "Română" },
  { code: "en", label: "English", native: "English" },
  { code: "es", label: "Spanish", native: "Español" },
  { code: "ru", label: "Russian", native: "Русский" },
  { code: "de", label: "German", native: "Deutsch" },
  { code: "fr", label: "French", native: "Français" },
  { code: "it", label: "Italian", native: "Italiano" },
  { code: "pt", label: "Portuguese", native: "Português" },
  { code: "ar", label: "Arabic", native: "العربية" },
  { code: "zh", label: "Chinese", native: "中文" },
  { code: "ja", label: "Japanese", native: "日本語" },
  { code: "hi", label: "Hindi", native: "हिन्दी" },
];

type Dict = Record<string, string>;

const STRINGS: Record<LangCode, Dict> = {
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
  },
  es: { app_name: "Astran Game", tagline: "Juega. Crea. Explora.", login: "Iniciar sesión", register: "Registro", logout: "Salir", email: "Correo", password: "Contraseña", username: "Usuario", continue: "Continuar", or: "o", google_login: "Continuar con Google", age_select_title: "Elige tu categoría de edad", age_select_subtitle: "Puedes cambiarlo en Ajustes", age_under_18: "Menor de 18", age_18_plus: "18+", age_under_desc: "Contenido más seguro", age_18_desc: "Acceso a juegos para adultos", discover: "Descubrir", studio: "Estudio", friends: "Amigos", profile: "Perfil", for_you: "Para ti", trending: "Tendencias", new_games: "Nuevos", popular: "Populares", recently_played: "Recientes", play: "Jugar", astrans: "Astrans", wallet: "Cartera", buy_astrans: "Comprar Astrans", transfer: "Transferir", transactions: "Transacciones", settings: "Ajustes", language: "Idioma", graphics: "Gráficos", view_distance: "Distancia", create_game: "Crear juego", my_games: "Mis juegos", search: "Buscar", add_friend: "Añadir", accept: "Aceptar", reject: "Rechazar", block: "Bloquear", remove: "Eliminar", online: "En línea", offline: "Desconectado", incoming: "Entrantes", outgoing: "Enviadas", reset: "Reiniciar", save: "Guardar", cancel: "Cancelar", welcome_bonus: "Bono de bienvenida", balance: "Saldo", fee: "Comisión", receive: "Recibe", send: "Enviar", game_title: "Título", game_desc: "Descripción", public_game: "Público", game_created: "¡Juego creado!", error: "Error", loading: "Cargando..." },
  ru: { app_name: "Astran Game", tagline: "Играй. Создавай. Исследуй.", login: "Войти", register: "Регистрация", logout: "Выйти", email: "Эл. почта", password: "Пароль", username: "Имя", continue: "Далее", or: "или", google_login: "Продолжить с Google", age_select_title: "Выберите категорию возраста", age_select_subtitle: "Можно изменить в настройках", age_under_18: "До 18", age_18_plus: "18+", age_under_desc: "Безопасный контент", age_18_desc: "Игры для взрослых", discover: "Обзор", studio: "Студия", friends: "Друзья", profile: "Профиль", for_you: "Для вас", trending: "В тренде", new_games: "Новое", popular: "Популярные", recently_played: "Недавние", play: "Играть", astrans: "Astrans", wallet: "Кошелёк", buy_astrans: "Купить Astrans", transfer: "Перевести", transactions: "Транзакции", settings: "Настройки", language: "Язык", graphics: "Графика", view_distance: "Дальность", create_game: "Создать игру", my_games: "Мои игры", search: "Поиск", add_friend: "Добавить", accept: "Принять", reject: "Отклонить", block: "Блок", remove: "Удалить", online: "В сети", offline: "Не в сети", incoming: "Входящие", outgoing: "Исходящие", reset: "Сброс", save: "Сохранить", cancel: "Отмена", welcome_bonus: "Приветственный бонус", balance: "Баланс", fee: "Комиссия", receive: "Получает", send: "Отправить", game_title: "Название", game_desc: "Описание", public_game: "Публичная", game_created: "Игра создана!", error: "Ошибка", loading: "Загрузка..." },
  de: { app_name: "Astran Game", tagline: "Spielen. Erschaffen. Erkunden.", login: "Anmelden", register: "Registrieren", logout: "Abmelden", email: "E-Mail", password: "Passwort", username: "Nutzername", continue: "Weiter", or: "oder", google_login: "Mit Google fortfahren", age_select_title: "Wähle deine Altersgruppe", age_select_subtitle: "Später änderbar", age_under_18: "Unter 18", age_18_plus: "18+", age_under_desc: "Sichere Inhalte", age_18_desc: "Erwachseneninhalte", discover: "Entdecken", studio: "Studio", friends: "Freunde", profile: "Profil", for_you: "Für dich", trending: "Trends", new_games: "Neu", popular: "Beliebt", recently_played: "Zuletzt", play: "Spielen", astrans: "Astrans", wallet: "Geldbörse", buy_astrans: "Astrans kaufen", transfer: "Übertragen", transactions: "Transaktionen", settings: "Einstellungen", language: "Sprache", graphics: "Grafik", view_distance: "Sichtweite", create_game: "Spiel erstellen", my_games: "Meine Spiele", search: "Suchen", add_friend: "Hinzufügen", accept: "Annehmen", reject: "Ablehnen", block: "Blockieren", remove: "Entfernen", online: "Online", offline: "Offline", incoming: "Eingang", outgoing: "Ausgang", reset: "Reset", save: "Speichern", cancel: "Abbrechen", welcome_bonus: "Willkommensbonus", balance: "Guthaben", fee: "Gebühr", receive: "Erhält", send: "Senden", game_title: "Titel", game_desc: "Beschreibung", public_game: "Öffentlich", game_created: "Spiel erstellt!", error: "Fehler", loading: "Lädt..." },
  fr: { app_name: "Astran Game", tagline: "Joue. Crée. Explore.", login: "Connexion", register: "S'inscrire", logout: "Déconnexion", email: "E-mail", password: "Mot de passe", username: "Nom", continue: "Continuer", or: "ou", google_login: "Continuer avec Google", age_select_title: "Choisis ta catégorie d'âge", age_select_subtitle: "Modifiable plus tard", age_under_18: "Moins de 18", age_18_plus: "18+", age_under_desc: "Contenu sécurisé", age_18_desc: "Jeux pour adultes", discover: "Découvrir", studio: "Studio", friends: "Amis", profile: "Profil", for_you: "Pour toi", trending: "Tendance", new_games: "Nouveaux", popular: "Populaires", recently_played: "Récents", play: "Jouer", astrans: "Astrans", wallet: "Portefeuille", buy_astrans: "Acheter", transfer: "Transférer", transactions: "Transactions", settings: "Paramètres", language: "Langue", graphics: "Graphismes", view_distance: "Distance", create_game: "Créer un jeu", my_games: "Mes jeux", search: "Recherche", add_friend: "Ajouter", accept: "Accepter", reject: "Refuser", block: "Bloquer", remove: "Retirer", online: "En ligne", offline: "Hors ligne", incoming: "Reçus", outgoing: "Envoyés", reset: "Reset", save: "Enregistrer", cancel: "Annuler", welcome_bonus: "Bonus bienvenue", balance: "Solde", fee: "Frais", receive: "Reçoit", send: "Envoyer", game_title: "Titre", game_desc: "Description", public_game: "Public", game_created: "Jeu créé !", error: "Erreur", loading: "Chargement..." },
  it: { app_name: "Astran Game", tagline: "Gioca. Crea. Esplora.", login: "Accedi", register: "Registrati", logout: "Esci", email: "Email", password: "Password", username: "Utente", continue: "Continua", or: "o", google_login: "Continua con Google", age_select_title: "Scegli categoria età", age_select_subtitle: "Modificabile", age_under_18: "Sotto 18", age_18_plus: "18+", age_under_desc: "Contenuti sicuri", age_18_desc: "Giochi adulti", discover: "Scopri", studio: "Studio", friends: "Amici", profile: "Profilo", for_you: "Per te", trending: "Tendenze", new_games: "Novità", popular: "Popolari", recently_played: "Recenti", play: "Gioca", astrans: "Astrans", wallet: "Portafoglio", buy_astrans: "Compra", transfer: "Trasferisci", transactions: "Transazioni", settings: "Impostazioni", language: "Lingua", graphics: "Grafica", view_distance: "Distanza", create_game: "Crea gioco", my_games: "I miei giochi", search: "Cerca", add_friend: "Aggiungi", accept: "Accetta", reject: "Rifiuta", block: "Blocca", remove: "Rimuovi", online: "Online", offline: "Offline", incoming: "In arrivo", outgoing: "Inviate", reset: "Reset", save: "Salva", cancel: "Annulla", welcome_bonus: "Bonus benvenuto", balance: "Saldo", fee: "Commissione", receive: "Riceve", send: "Invia", game_title: "Titolo", game_desc: "Descrizione", public_game: "Pubblico", game_created: "Gioco creato!", error: "Errore", loading: "Caricamento..." },
  pt: { app_name: "Astran Game", tagline: "Joga. Cria. Explora.", login: "Entrar", register: "Registar", logout: "Sair", email: "E-mail", password: "Senha", username: "Utilizador", continue: "Continuar", or: "ou", google_login: "Continuar com Google", age_select_title: "Escolhe a categoria de idade", age_select_subtitle: "Podes mudar depois", age_under_18: "Menor 18", age_18_plus: "18+", age_under_desc: "Conteúdo seguro", age_18_desc: "Jogos adultos", discover: "Descobrir", studio: "Estúdio", friends: "Amigos", profile: "Perfil", for_you: "Para ti", trending: "Tendências", new_games: "Novos", popular: "Populares", recently_played: "Recentes", play: "Jogar", astrans: "Astrans", wallet: "Carteira", buy_astrans: "Comprar", transfer: "Transferir", transactions: "Transações", settings: "Definições", language: "Idioma", graphics: "Gráficos", view_distance: "Distância", create_game: "Criar jogo", my_games: "Meus jogos", search: "Procurar", add_friend: "Adicionar", accept: "Aceitar", reject: "Recusar", block: "Bloquear", remove: "Remover", online: "Online", offline: "Offline", incoming: "Recebidos", outgoing: "Enviados", reset: "Reiniciar", save: "Guardar", cancel: "Cancelar", welcome_bonus: "Bónus", balance: "Saldo", fee: "Taxa", receive: "Recebe", send: "Enviar", game_title: "Título", game_desc: "Descrição", public_game: "Público", game_created: "Jogo criado!", error: "Erro", loading: "A carregar..." },
  ar: { app_name: "Astran Game", tagline: "العب. أنشئ. استكشف.", login: "تسجيل الدخول", register: "إنشاء حساب", logout: "خروج", email: "البريد", password: "كلمة السر", username: "المستخدم", continue: "متابعة", or: "أو", google_login: "المتابعة مع Google", age_select_title: "اختر فئة عمرك", age_select_subtitle: "يمكن التغيير لاحقاً", age_under_18: "أقل من 18", age_18_plus: "+18", age_under_desc: "محتوى آمن", age_18_desc: "ألعاب البالغين", discover: "اكتشف", studio: "الاستوديو", friends: "الأصدقاء", profile: "الملف", for_you: "لك", trending: "الشائع", new_games: "جديد", popular: "الأكثر", recently_played: "الأخيرة", play: "العب", astrans: "Astrans", wallet: "المحفظة", buy_astrans: "شراء", transfer: "تحويل", transactions: "المعاملات", settings: "الإعدادات", language: "اللغة", graphics: "الرسوميات", view_distance: "المسافة", create_game: "إنشاء لعبة", my_games: "ألعابي", search: "بحث", add_friend: "إضافة", accept: "قبول", reject: "رفض", block: "حظر", remove: "إزالة", online: "متصل", offline: "غير متصل", incoming: "واردة", outgoing: "صادرة", reset: "إعادة", save: "حفظ", cancel: "إلغاء", welcome_bonus: "مكافأة", balance: "الرصيد", fee: "الرسوم", receive: "يستلم", send: "إرسال", game_title: "العنوان", game_desc: "الوصف", public_game: "عامة", game_created: "تم!", error: "خطأ", loading: "تحميل..." },
  zh: { app_name: "Astran Game", tagline: "玩。创造。探索。", login: "登录", register: "注册", logout: "退出", email: "邮箱", password: "密码", username: "用户名", continue: "继续", or: "或", google_login: "使用 Google 继续", age_select_title: "选择年龄类别", age_select_subtitle: "可稍后更改", age_under_18: "18岁以下", age_18_plus: "18+", age_under_desc: "更安全的内容", age_18_desc: "成人游戏", discover: "发现", studio: "工作室", friends: "好友", profile: "资料", for_you: "为你推荐", trending: "热门", new_games: "新作", popular: "流行", recently_played: "最近", play: "游玩", astrans: "Astrans", wallet: "钱包", buy_astrans: "购买", transfer: "转账", transactions: "交易", settings: "设置", language: "语言", graphics: "画质", view_distance: "视距", create_game: "创建游戏", my_games: "我的游戏", search: "搜索", add_friend: "添加", accept: "接受", reject: "拒绝", block: "屏蔽", remove: "移除", online: "在线", offline: "离线", incoming: "收到", outgoing: "发出", reset: "重置", save: "保存", cancel: "取消", welcome_bonus: "欢迎奖励", balance: "余额", fee: "手续费", receive: "收到", send: "发送", game_title: "标题", game_desc: "描述", public_game: "公开", game_created: "已创建!", error: "错误", loading: "加载中..." },
  ja: { app_name: "Astran Game", tagline: "遊ぶ。作る。探検する。", login: "ログイン", register: "登録", logout: "ログアウト", email: "メール", password: "パスワード", username: "ユーザー名", continue: "続ける", or: "または", google_login: "Googleで続行", age_select_title: "年齢カテゴリを選択", age_select_subtitle: "後で変更可能", age_under_18: "18歳未満", age_18_plus: "18+", age_under_desc: "安全なコンテンツ", age_18_desc: "大人向け", discover: "発見", studio: "スタジオ", friends: "フレンド", profile: "プロフィール", for_you: "おすすめ", trending: "急上昇", new_games: "新着", popular: "人気", recently_played: "最近", play: "プレイ", astrans: "Astrans", wallet: "ウォレット", buy_astrans: "購入", transfer: "送金", transactions: "取引", settings: "設定", language: "言語", graphics: "グラフィック", view_distance: "描画距離", create_game: "ゲーム作成", my_games: "マイゲーム", search: "検索", add_friend: "追加", accept: "承認", reject: "拒否", block: "ブロック", remove: "削除", online: "オンライン", offline: "オフライン", incoming: "受信", outgoing: "送信", reset: "リセット", save: "保存", cancel: "キャンセル", welcome_bonus: "ボーナス", balance: "残高", fee: "手数料", receive: "受取", send: "送信", game_title: "タイトル", game_desc: "説明", public_game: "公開", game_created: "作成!", error: "エラー", loading: "読込中..." },
  hi: { app_name: "Astran Game", tagline: "खेलो. बनाओ. खोजो.", login: "लॉगिन", register: "साइन अप", logout: "लॉग आउट", email: "ईमेल", password: "पासवर्ड", username: "यूजरनेम", continue: "जारी", or: "या", google_login: "Google से जारी रखें", age_select_title: "आयु श्रेणी चुनें", age_select_subtitle: "बाद में बदलें", age_under_18: "18 से कम", age_18_plus: "18+", age_under_desc: "सुरक्षित सामग्री", age_18_desc: "वयस्क गेम", discover: "खोजें", studio: "स्टूडियो", friends: "दोस्त", profile: "प्रोफाइल", for_you: "आपके लिए", trending: "ट्रेंडिंग", new_games: "नए", popular: "लोकप्रिय", recently_played: "हाल के", play: "खेलें", astrans: "Astrans", wallet: "वॉलेट", buy_astrans: "खरीदें", transfer: "भेजें", transactions: "लेनदेन", settings: "सेटिंग्स", language: "भाषा", graphics: "ग्राफिक्स", view_distance: "दूरी", create_game: "गेम बनाएँ", my_games: "मेरे गेम", search: "खोज", add_friend: "जोड़ें", accept: "स्वीकार", reject: "अस्वीकार", block: "ब्लॉक", remove: "हटाएँ", online: "ऑनलाइन", offline: "ऑफलाइन", incoming: "आने वाले", outgoing: "जाने वाले", reset: "रीसेट", save: "सहेजें", cancel: "रद्द", welcome_bonus: "बोनस", balance: "बैलेंस", fee: "शुल्क", receive: "प्राप्त", send: "भेजें", game_title: "शीर्षक", game_desc: "विवरण", public_game: "सार्वजनिक", game_created: "बनाया!", error: "त्रुटि", loading: "लोड हो रहा है..." },
};

export function detectDeviceLang(): LangCode {
  try {
    const locales = getLocales();
    const code = (locales?.[0]?.languageCode || "en").toLowerCase();
    if ((STRINGS as any)[code]) return code as LangCode;
  } catch {}
  return "en";
}

type Ctx = { lang: LangCode; setLang: (l: LangCode) => void; t: (k: keyof typeof STRINGS["ro"]) => string };

const I18nContext = React.createContext<Ctx>({
  lang: "ro",
  setLang: () => {},
  t: (k) => STRINGS.ro[k as string] ?? String(k),
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = React.useState<LangCode>("ro");

  React.useEffect(() => {
    (async () => {
      const saved = await AsyncStorage.getItem("astran_lang");
      if (saved && (STRINGS as any)[saved]) setLangState(saved as LangCode);
      else setLangState(detectDeviceLang());
    })();
  }, []);

  const setLang = React.useCallback((l: LangCode) => {
    setLangState(l);
    AsyncStorage.setItem("astran_lang", l).catch(() => {});
  }, []);

  const t = React.useCallback(
    (k: keyof typeof STRINGS["ro"]) => (STRINGS[lang] as Dict)[k as string] ?? STRINGS.en[k as string] ?? String(k),
    [lang]
  );

  return React.createElement(I18nContext.Provider, { value: { lang, setLang, t } }, children);
}

export function useI18n() {
  return React.useContext(I18nContext);
}