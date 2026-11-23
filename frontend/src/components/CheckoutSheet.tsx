import React, { useEffect, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { colors, radius, spacing } from "@/src/theme";
import { useI18n } from "@/src/i18n";
import { cr, crErr } from "@/src/i18n/consumerStrings";
import { consumerApi, type Checkout, type ProductType } from "@/src/api/consumer";
import { Check } from "@/src/components/modUi";
import ConsumerDocModal from "@/src/components/ConsumerDocModal";
import LegalDocModal from "@/src/components/LegalDocModal";

/**
 * Ecranul de achizitie cu informatiile precontractuale (art. 6) si cele DOUA confirmari (art. 16 lit. (m)).
 * Pretul, textele si versiunile vin de la server; casutele NU sunt bifate implicit; butonul ramane dezactivat pana la ambele bife.
 * Serverul re-valideaza totul la achizitie: acest ecran nu e dovada, ci doar colecteaza consimtamantul si il trimite la server.
 */
export function CheckoutSheet({ visible, productType, productId, onClose, onConfirmed }: {
  visible: boolean; productType: ProductType; productId: string; onClose: () => void; onConfirmed: (checkoutId: string) => void;
}) {
  const { lang } = useI18n();
  const [doc, setDoc] = useState<"refund" | "terms" | null>(null);
  const [co, setCo] = useState<Checkout | null>(null);
  const [a, setA] = useState(false);
  const [b, setB] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    let live = true;
    setCo(null); setA(false); setB(false); setErr(null);
    consumerApi.createCheckout(productType, productId, lang).then(c => { if (live) setCo(c); }).catch(e => { if (live) setErr(crErr(lang, e)); });
    return () => { live = false; };
  }, [visible, productType, productId, lang]);

  const needs = !!co?.needs_consent;
  const ready = !!co && (!needs || (a && b)) && !busy;

  async function confirm() {
    if (!co || !ready) return;
    setBusy(true); setErr(null);
    try {
      if (needs) await consumerApi.confirmCheckout(co);   // serverul inregistreaza separat fiecare confirmare
      onConfirmed(co.checkout_id);
    } catch (e: any) { setErr(crErr(lang, e)); } finally { setBusy(false); }
  }
  const t = co?.trader;
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose} transparent={false}>
      <SafeAreaView style={s.root} edges={["top", "bottom"]}>
        <View style={s.header}><Text style={s.title}>{cr(lang, "checkout_title")}</Text></View>
        <ScrollView contentContainerStyle={s.content}>
          <View style={s.column}>
            {!co && !err ? <ActivityIndicator color={colors.brand} style={{ margin: 24 }} /> : null}
            {co ? (
              <>
                <Row k={cr(lang, "product")} v={co.product.name} bold />
                <Row k={cr(lang, "description")} v={co.product.description} />
                <Row k={cr(lang, "price")} v={`${co.product.price} ${co.product.currency}`} bold />
                <Row k={cr(lang, "fees")} v={cr(lang, "fees_none")} />
                <Row k={cr(lang, "delivery")} v={co.delivery_note} />
                <Row k={cr(lang, "withdrawal")} v={co.withdrawal.info} />
                <Row k={cr(lang, "trader")} v={[t?.name, t?.address, t?.email, t?.phone, t?.registration_no, t?.vat_id].filter(Boolean).join("\n")} />
                <View style={s.links}>
                  <Pressable onPress={() => setDoc("refund")} testID="checkout-refund-policy"><Text style={s.link}>{cr(lang, "refund_policy")} (v{co.terms.refund_policy_version})</Text></Pressable>
                  <Pressable onPress={() => setDoc("terms")} testID="checkout-terms"><Text style={s.link}>{cr(lang, "terms")} (v{co.terms.terms_of_service_version})</Text></Pressable>
                </View>
                {needs && co.consent_texts ? (
                  <View style={s.consent}>
                    <Check checked={a} onToggle={() => setA(v => !v)} label={co.consent_texts.A.text} testID="checkout-consent-a" />
                    <Check checked={b} onToggle={() => setB(v => !v)} label={co.consent_texts.B.text} testID="checkout-consent-b" />
                    {!(a && b) ? <Text style={s.hint}>{cr(lang, "confirm_hint")}</Text> : null}
                  </View>
                ) : null}
              </>
            ) : null}
            {err ? <Text style={s.err} testID="checkout-error">{err}</Text> : null}
          </View>
        </ScrollView>
        <View style={s.footer}>
          <View style={s.column}>
            <Pressable testID="checkout-confirm" disabled={!ready} onPress={confirm} style={[s.btn, !ready && { opacity: 0.4 }]}>
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={s.btnTx}>{cr(lang, "buy_now")}</Text>}
            </Pressable>
            <Pressable testID="checkout-cancel" onPress={onClose} style={s.ghost}><Text style={s.ghostTx}>{cr(lang, "cancel")}</Text></Pressable>
          </View>
        </View>
      </SafeAreaView>
      {doc === "refund" ? <ConsumerDocModal visible onClose={() => setDoc(null)} /> : null}
      {doc === "terms" ? <LegalDocModal visible docType="terms_of_service" lang={lang} onClose={() => setDoc(null)} /> : null}
    </Modal>
  );
}

function Row({ k, v, bold }: { k: string; v: string; bold?: boolean }) {
  return <View style={{ marginBottom: 12 }}><Text style={s.k}>{k}</Text><Text style={[s.v, bold && { fontWeight: "700" }]} selectable>{v}</Text></View>;
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { padding: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.border, alignItems: "center" },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "700" },
  content: { padding: spacing.md, alignItems: "center" },
  column: { width: "100%", maxWidth: 640 },     // telefon: latime completa; desktop/web: coloana centrata
  k: { color: colors.onSurface3, fontSize: 12, textTransform: "uppercase", marginBottom: 2 },
  v: { color: colors.onSurface, fontSize: 15, lineHeight: 21 },
  links: { gap: 8, marginBottom: 16 },
  link: { color: colors.brand, textDecorationLine: "underline", fontSize: 14 },
  consent: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: spacing.md, marginBottom: 8 },
  hint: { color: colors.onSurface3, fontSize: 12 },
  err: { color: colors.error, marginTop: 8 },
  footer: { padding: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, alignItems: "center" },
  btn: { backgroundColor: colors.brand, borderRadius: radius.md, paddingVertical: 14, alignItems: "center" },
  btnTx: { color: "#fff", fontWeight: "700", fontSize: 16 },
  ghost: { paddingVertical: 12, alignItems: "center" },
  ghostTx: { color: colors.onSurface2 },
});
