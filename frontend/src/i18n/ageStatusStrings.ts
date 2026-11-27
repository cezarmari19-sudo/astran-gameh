// Texte pentru ecranul „Vârstă și acces”. Statusurile și motivele vin de la server (age_entitlement_service); aici doar le traducem.
type S = Record<string, string>;

const RO: S = {
  title: "Vârstă și acces", declared: "Vârsta declarată", status: "Starea verificării", available: "Funcții disponibile", restricted: "Funcții restricționate",
  band_under_16: "Sub 16 ani", band_16_17: "16–17 ani", band_18_plus: "18+ (declarat)", band_none: "Nedeclarată",
  "st_not_verified": "Neverificat", "st_verification_pending": "Verificare în așteptare", "st_verified": "Verificat", "st_verification_failed": "Verificare eșuată",
  "st_parental_consent_required": "Consimțământ parental necesar", "st_parental_consent_pending": "Consimțământ parental în așteptare",
  "st_parental_consent_revoked": "Consimțământ parental retras", "st_restricted": "Restricționat",
  f_play: "Jocuri potrivite vârstei", f_friends: "Prieteni și cereri", f_block: "Blocare utilizatori", f_dm: "Mesaje private și chat", f_groups: "Grupuri",
  f_wallet_view: "Portofel (vizualizare)", f_shop_browse: "Magazin (răsfoire)", f_shop_purchase: "Magazin (cumpărare cu Astrans)",
  f_real_money_purchase: "Cumpărare Astrans cu bani reali", f_transfer: "Transfer între utilizatori", f_game_publish: "Publicare jocuri",
  f_creator_monetization: "Vânzare și monetizare creator", f_adult_content: "Conținut 18+",
  r_account_restricted: "Contul este restricționat până se completează informațiile despre vârstă.",
  r_account_suspended: "Contul este suspendat de moderare.",
  r_age_info_required: "Introdu data nașterii ca să continui.",
  r_parental_consent_required: "Contul are sub 16 ani: este necesar consimțământul unui părinte sau tutore.",
  r_parental_consent_pending: "Cererea către părinte este în așteptare; funcția se deblochează după verificarea și aprobarea lui.",
  r_parental_consent_revoked: "Consimțământul parental a fost retras; funcțiile care depind de el au fost oprite.",
  r_age_verification_required: "Vârsta declarată nu este dovadă. Funcția cere verificarea vârstei de către un serviciu extern.",
  r_parental_scope_required: "Părintele trebuie să aprobe separat această funcție.",
  r_adult_verified_only: "Doar pentru adulți cu vârsta verificată (18+).",
  r_not_available_to_minors: "Nu este disponibil conturilor sub 18 ani.",
  r_recipient_not_eligible: "Destinatarul nu este eligibil.",
  next_verify: "Începe verificarea vârstei", next_parent: "Cere consimțământ parental", next_dob: "Introdu data nașterii",
  unavailable: "Verificare indisponibilă: furnizorul nu este configurat încă. Nu ți-am marcat contul ca verificat.",
  pending_note: "Verificare în așteptare. Rezultatul vine de la furnizor; nu poate fi bifat din aplicație.",
  failed_note: "Verificarea a eșuat. Poți încerca din nou; contul rămâne cu funcțiile limitate până atunci.",
  revoked_note: "Părintele a retras consimțământul. Poți cere unul nou.", scopes: "Aprobări parentale active", back: "Înapoi",
};
RO.refresh = "Reîncarcă";

const EN: S = {
  title: "Age and access", declared: "Declared age", status: "Verification status", available: "Available features", restricted: "Restricted features",
  band_under_16: "Under 16", band_16_17: "16–17", band_18_plus: "18+ (declared)", band_none: "Not declared",
  "st_not_verified": "Not verified", "st_verification_pending": "Verification pending", "st_verified": "Verified", "st_verification_failed": "Verification failed",
  "st_parental_consent_required": "Parental consent required", "st_parental_consent_pending": "Parental consent pending",
  "st_parental_consent_revoked": "Parental consent revoked", "st_restricted": "Restricted",
  f_play: "Age-appropriate games", f_friends: "Friends and requests", f_block: "Blocking users", f_dm: "Private messages and chat", f_groups: "Groups",
  f_wallet_view: "Wallet (view)", f_shop_browse: "Shop (browse)", f_shop_purchase: "Shop (buy with Astrans)",
  f_real_money_purchase: "Buy Astrans with real money", f_transfer: "Transfers between users", f_game_publish: "Game publishing",
  f_creator_monetization: "Selling and creator monetization", f_adult_content: "18+ content",
  r_account_restricted: "The account is restricted until age information is completed.",
  r_account_suspended: "The account is suspended by moderation.",
  r_age_info_required: "Enter your date of birth to continue.",
  r_parental_consent_required: "The account is under 16: a parent or guardian must give consent.",
  r_parental_consent_pending: "The request to your parent is pending; this unlocks after they are verified and approve.",
  r_parental_consent_revoked: "Parental consent was withdrawn; features that depend on it were turned off.",
  r_age_verification_required: "A declared age is not proof. This feature needs an age check by an external service.",
  r_parental_scope_required: "A parent must approve this feature separately.",
  r_adult_verified_only: "Only for adults with a verified age (18+).",
  r_not_available_to_minors: "Not available to accounts under 18.",
  r_recipient_not_eligible: "The recipient is not eligible.",
  next_verify: "Start age verification", next_parent: "Request parental consent", next_dob: "Enter date of birth",
  unavailable: "Verification unavailable: the provider is not configured yet. Your account was not marked as verified.",
  pending_note: "Verification pending. The result comes from the provider; it cannot be ticked in the app.",
  failed_note: "Verification failed. You can try again; the account keeps limited features until then.",
  revoked_note: "Your parent withdrew consent. You can request a new one.", scopes: "Active parental approvals", refresh: "Refresh", back: "Back",
};

export function ass(lang: string, key: string): string {
  return ((lang === "ro" ? RO : EN) as S)[key] ?? EN[key] ?? key;
}
