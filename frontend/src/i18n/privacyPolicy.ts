import { LangCode } from "./index";
import { PRIVACY_POLICY_RO } from "./privacyPolicy.ro";
import { PRIVACY_POLICY_EN } from "./privacyPolicy.en";
import { PRIVACY_POLICY_ES } from "./privacyPolicy.es";
import { PRIVACY_POLICY_FR } from "./privacyPolicy.fr";
import { PRIVACY_POLICY_PL } from "./privacyPolicy.pl";
import { PRIVACY_POLICY_NL } from "./privacyPolicy.nl";
import { PRIVACY_POLICY_EL } from "./privacyPolicy.el";
import { PRIVACY_POLICY_SV } from "./privacyPolicy.sv";
import { PRIVACY_POLICY_DA } from "./privacyPolicy.da";
import { PRIVACY_POLICY_FI } from "./privacyPolicy.fi";
import { PRIVACY_POLICY_NO } from "./privacyPolicy.no";
import { PRIVACY_POLICY_IS } from "./privacyPolicy.is";
import { PRIVACY_POLICY_CS } from "./privacyPolicy.cs";
import { PRIVACY_POLICY_SK } from "./privacyPolicy.sk";
import { PRIVACY_POLICY_HU } from "./privacyPolicy.hu";
import { PRIVACY_POLICY_BG } from "./privacyPolicy.bg";
import { PRIVACY_POLICY_HR } from "./privacyPolicy.hr";
import { PRIVACY_POLICY_SR } from "./privacyPolicy.sr";
import { PRIVACY_POLICY_SL } from "./privacyPolicy.sl";
import { PRIVACY_POLICY_BS } from "./privacyPolicy.bs";
import { PRIVACY_POLICY_MK } from "./privacyPolicy.mk";
import { PRIVACY_POLICY_SQ } from "./privacyPolicy.sq";
import { PRIVACY_POLICY_UK } from "./privacyPolicy.uk";
import { PRIVACY_POLICY_BE } from "./privacyPolicy.be";
import { PRIVACY_POLICY_LT } from "./privacyPolicy.lt";
import { PRIVACY_POLICY_LV } from "./privacyPolicy.lv";
import { PRIVACY_POLICY_ET } from "./privacyPolicy.et";
import { PRIVACY_POLICY_MT } from "./privacyPolicy.mt";
import { PRIVACY_POLICY_TR } from "./privacyPolicy.tr";
import { PRIVACY_POLICY_KK } from "./privacyPolicy.kk";
import { PRIVACY_POLICY_UR } from "./privacyPolicy.ur";
import { PRIVACY_POLICY_KO } from "./privacyPolicy.ko";
import { PRIVACY_POLICY_HE } from "./privacyPolicy.he";
import { PRIVACY_POLICY_FA } from "./privacyPolicy.fa";
// ro, en, es, fr, pl, nl, el, sv, da, fi, no, is, cs, sk, hu, bg, hr, sr, sl, bs, mk, sq, uk,
// be, lt, lv, et, mt, tr, kk, ur, ko, he, fa = 34 fisiere separate (ja, zh, ar, hi, ru sunt
// deja complete in privacyPolicy.ro.ts/.en.ts... nu - vezi nota de mai jos).

export const PRIVACY_POLICY: Partial<Record<LangCode, string>> = {
  ro: PRIVACY_POLICY_RO,
  en: PRIVACY_POLICY_EN,
  es: PRIVACY_POLICY_ES,
  fr: PRIVACY_POLICY_FR,
  pl: PRIVACY_POLICY_PL,
  nl: PRIVACY_POLICY_NL,
  el: PRIVACY_POLICY_EL,
  sv: PRIVACY_POLICY_SV,
  da: PRIVACY_POLICY_DA,
  fi: PRIVACY_POLICY_FI,
  no: PRIVACY_POLICY_NO,
  is: PRIVACY_POLICY_IS,
  cs: PRIVACY_POLICY_CS,
  sk: PRIVACY_POLICY_SK,
  hu: PRIVACY_POLICY_HU,
  bg: PRIVACY_POLICY_BG,
  hr: PRIVACY_POLICY_HR,
  sr: PRIVACY_POLICY_SR,
  sl: PRIVACY_POLICY_SL,
  bs: PRIVACY_POLICY_BS,
  mk: PRIVACY_POLICY_MK,
  sq: PRIVACY_POLICY_SQ,
  uk: PRIVACY_POLICY_UK,
  be: PRIVACY_POLICY_BE,
  lt: PRIVACY_POLICY_LT,
  lv: PRIVACY_POLICY_LV,
  et: PRIVACY_POLICY_ET,
  mt: PRIVACY_POLICY_MT,
  tr: PRIVACY_POLICY_TR,
  kk: PRIVACY_POLICY_KK,
  ur: PRIVACY_POLICY_UR,
  ko: PRIVACY_POLICY_KO,
  he: PRIVACY_POLICY_HE,
  fa: PRIVACY_POLICY_FA,
};

export function privacyPolicyText(lang: LangCode): string {
  return PRIVACY_POLICY[lang] ?? PRIVACY_POLICY.en ?? PRIVACY_POLICY.ro!;
}