import { LangCode } from "./index";
import { PRIVACY_POLICY_RO } from "./privacyPolicy.ro";
import { PRIVACY_POLICY_EN } from "./privacyPolicy.en";

// Aplicatia foloseste doar romana si engleza (LangCode). Textele sunt oglinzi GENERATE din backend/legal/privacy_policy
// (sursa versionata, cu SHA-256). Celelalte fisiere privacyPolicy.<limba>.ts NU sunt referite de aplicatie.
export const PRIVACY_POLICY: Record<LangCode, string> = {
  ro: PRIVACY_POLICY_RO,
  en: PRIVACY_POLICY_EN,
};

export function privacyPolicyText(lang: LangCode): string {
  return PRIVACY_POLICY[lang] ?? PRIVACY_POLICY.en;
}
