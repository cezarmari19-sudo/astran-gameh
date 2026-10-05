import { LangCode } from "./index";
import { PRIVACY_POLICY_RO } from "./privacyPolicy.ro";
import { PRIVACY_POLICY_EN } from "./privacyPolicy.en";
import { PRIVACY_POLICY_FR } from "./privacyPolicy.fr";
import { PRIVACY_POLICY_ES } from "./privacyPolicy.es";
import { PRIVACY_POLICY_PL } from "./privacyPolicy.pl";
import { PRIVACY_POLICY_NL } from "./privacyPolicy.nl";
import { PRIVACY_POLICY_EL } from "./privacyPolicy.el";

// Fiecare limba traista e intr-un fisier separat (privacyPolicy.<cod>.ts), ca sa ramana sub
// limita de clipboard a utilizatorului si usor de adaugat pe rand, fara sa retrimitem tot ce
// exista deja. Adaugarea unei limbi noi = 1 fisier nou + 1 linie de import + 1 linie in obiectul
// de mai jos - niciodata o rescriere a limbilor deja gata.
export const PRIVACY_POLICY: Partial<Record<LangCode, string>> = {
  ro: PRIVACY_POLICY_RO,
  en: PRIVACY_POLICY_EN,
  fr: PRIVACY_POLICY_FR,
  es: PRIVACY_POLICY_ES,
  pl: PRIVACY_POLICY_PL,
  nl: PRIVACY_POLICY_NL,
  el: PRIVACY_POLICY_EL,
};

export function privacyPolicyText(lang: LangCode): string {
  return PRIVACY_POLICY[lang] ?? PRIVACY_POLICY.en ?? PRIVACY_POLICY.ro!;
}