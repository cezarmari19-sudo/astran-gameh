import React, { useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { as } from "@/src/i18n/ageStrings";
import { ass } from "@/src/i18n/ageStatusStrings";
import { api } from "@/src/api/client";
import { EmailVerifyCard } from "@/src/components/EmailVerifyCard";
import PrivacyDataSection from "@/src/components/PrivacyDataSection";
import PersonalDataSection from "@/src/components/PersonalDataSection";
import RecommendationControls from "@/src/components/RecommendationControls";
import { cs } from "@/src/i18n/complianceStrings";
import LegalDocModal from "@/src/components/LegalDocModal";
import ConsumerDocModal from "@/src/components/ConsumerDocModal";
import { cr } from "@/src/i18n/consumerStrings";
import { ms } from "@/src/i18n/modStrings";
import { colors, radius, spacing } from "@/src/theme";

export default function SettingsScreen() {
  const router = useRouter();
  const { user, refresh } = useAuth();
  const { t } = useI18n();
  const [graphics, setGraphics] = useState(2);
  const [distance, setDistance] = useState(2);
  const [terms, setTerms] = useState(false);
  const [refundPolicy, setRefundPolicy] = useState(false);
  // Categoria de varsta NU mai e editabila din aplicatie: e calculata de server din data nasterii, iar 18+ cere
  // verificare a varstei (age assurance). Aici doar o afisam si oferim caile corecte.
  const { lang } = useI18n();
  const ageLabel = user?.adult_content_allowed ? as(lang, "av_status_verified")
    : user?.age_band === "under_16" ? "<16" : user?.age_band === "16_17" ? "16–17" : user?.age_band === "18_plus" ? "18+ (" + as(lang, "av_status_none") + ")" : "?";

  const Slider = ({ value, onChange, testID }: any) => (
    <View style={styles.sliderRow} testID={testID}>
      {[0, 1, 2, 3, 4].map(i => (
        <Pressable key={i} onPress={() => onChange(i)} style={[styles.slot, value >= i && styles.slotOn]} testID={`${testID}-${i}`} />
      ))}
    </View>
  );

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} testID="settings-back"><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.title}>{t("settings")}</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 40 }}>
        <EmailVerifyCard />
        <Text style={styles.section}>{t("language").toUpperCase()}</Text>
        <Pressable onPress={() => router.push("/language")} style={styles.row} testID="settings-language">
          <MaterialCommunityIcons name="translate" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{t("language")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>

        <Text style={styles.section}>{as(lang, "av_title").toUpperCase()}</Text>
        <Text style={{ color: colors.onSurface2, fontSize: 14 }} testID="settings-age-status">{ageLabel}</Text>
        <Pressable testID="settings-age-status-page" onPress={() => router.push("/age-status")} style={[styles.ageBtn, { marginTop: 8 }]}>
          <Text style={styles.ageBtnText}>{ass(lang, "title")}</Text>
        </Pressable>
        <Pressable testID="settings-age-verify" onPress={() => router.push("/(onboarding)/age-verify")} style={[styles.ageBtn, { marginTop: 8 }]}>
          <Text style={styles.ageBtnText}>{as(lang, "av_title")}</Text>
        </Pressable>
        <Pressable testID="settings-parental-panel" onPress={() => router.push("/parental/panel")} style={[styles.ageBtn, { marginTop: 8 }]}>
          <Text style={styles.ageBtnText}>{as(lang, "pp_title")}</Text>
        </Pressable>

        <Text style={styles.section}>{t("graphics").toUpperCase()}</Text>
        <Slider value={graphics} onChange={setGraphics} testID="graphics-slider" />
        <Text style={styles.hint}>Independent from view distance</Text>

        <Text style={styles.section}>{t("view_distance").toUpperCase()}</Text>
        <Slider value={distance} onChange={setDistance} testID="distance-slider" />
        {graphics >= 3 && distance >= 3 ? (
          <Text style={styles.warn}>⚠ High graphics + high view distance can reduce performance on older devices.</Text>
        ) : null}

        <Text style={styles.section}>{ms(lang, "mod_title").toUpperCase()}</Text>
        <Pressable onPress={() => setTerms(true)} style={styles.row} testID="settings-terms">
          <MaterialCommunityIcons name="file-document-outline" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{ms(lang, "mod_terms")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>
        <Pressable onPress={() => router.push("/moderation")} style={[styles.row, { marginTop: 8 }]} testID="settings-moderation">
          <MaterialCommunityIcons name="gavel" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{ms(lang, "mod_decisions")} · {ms(lang, "mod_my_reports")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>
        <Pressable onPress={() => router.push("/moderation/contact")} style={[styles.row, { marginTop: 8 }]} testID="settings-contact">
          <MaterialCommunityIcons name="email-outline" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{ms(lang, "mod_contact")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>
        <Pressable onPress={() => setRefundPolicy(true)} style={[styles.row, { marginTop: 8 }]} testID="settings-refund-policy">
          <MaterialCommunityIcons name="cash-refund" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{cr(lang, "policy_open")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>
        <Pressable onPress={() => router.push("/refunds")} style={[styles.row, { marginTop: 8 }]} testID="settings-refunds">
          <MaterialCommunityIcons name="receipt-text-outline" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{cr(lang, "refund_requests")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>
        {refundPolicy ? <ConsumerDocModal visible onClose={() => setRefundPolicy(false)} /> : null}
        {terms ? <LegalDocModal visible docType="terms_of_service" lang={lang} onClose={() => setTerms(false)} /> : null}

        <Text style={styles.section}>{cs(lang, "pd_title").toUpperCase()}</Text>
        <PersonalDataSection />
        <RecommendationControls onOpenTerms={() => setTerms(true)} />
        <Pressable onPress={() => router.push("/seller")} style={[styles.row, { marginTop: 8 }]} testID="settings-seller">
          <MaterialCommunityIcons name="storefront-outline" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{cs(lang, "sl_open")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>

        <PrivacyDataSection />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.lg },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  section: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 2, marginTop: 22, marginBottom: 10 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  rowText: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  ageRow: { flexDirection: "row", gap: 8 },
  ageBtn: { flex: 1, padding: 14, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  ageBtnActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  ageBtnText: { color: colors.onSurface2, fontWeight: "700" },
  sliderRow: { flexDirection: "row", gap: 6 },
  slot: { flex: 1, height: 14, borderRadius: 7, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  slotOn: { backgroundColor: colors.brand, borderColor: colors.brand },
  hint: { color: colors.onSurface3, fontSize: 11, marginTop: 6 },
  warn: { color: colors.warning, fontSize: 12, marginTop: 8, fontWeight: "600" },
});