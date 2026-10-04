import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator, Modal, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useIAP } from "expo-iap";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

export default function WalletScreen() {
  const router = useRouter();
  const { user, refresh } = useAuth();
  const { t } = useI18n();
  const [packages, setPackages] = useState<any[]>([]);
  const [currency, setCurrency] = useState("RON");
  const [transactions, setTransactions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showTransfer, setShowTransfer] = useState(false);
  const [transferTo, setTransferTo] = useState("");
  const [transferAmt, setTransferAmt] = useState("");
  const [transferPreview, setTransferPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Mesaj special pentru cazul rar in care Google/Apple au confirmat plata, dar creditarea
  // pe server a esuat (retea, server jos) - vezi handlePurchaseSuccess mai jos. Diferit de
  // `err` (eroare normala de achizitie), ca userul sa stie ca BANII AU FOST LUATI, nu doar
  // ca achizitia a esuat, si sa nu incerce sa plateasca din nou.
  const [pendingCreditWarning, setPendingCreditWarning] = useState<string | null>(null);

  // IAP real (Google Play pe Android, Apple App Store pe iOS) - vezi expo-iap. Pe web
  // acest hook nu face nimic (connected ramane false), fallback-ul ramane mesajul de mai
  // jos ca achizitiile nu sunt disponibile pe web.
  const { connected, products, fetchProducts, requestPurchase, finishTransaction } = useIAP({
    onPurchaseSuccess: (purchase: any) => {
      handlePurchaseSuccess(purchase);
    },
    onPurchaseError: (error: any) => {
      console.log("[wallet] purchase error", error);
      setErr(error?.message || "Purchase failed");
      setBusy(false);
    },
  });

  const load = useCallback(async () => {
    try {
      const [p, tx] = await Promise.all([api("/wallet/packages"), api("/wallet/transactions")]);
      setPackages(p.packages || []);
      setCurrency(p.currency || "RON");
      setTransactions(tx.transactions || []);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Produsele reale (nume, preT localizat) se incarca de la Google/Apple abia dupa ce
  // STIM ce package_id-uri exista (din backend) SI magazinul e conectat - SKU-urile
  // trimise sunt EXACT package_id-urile din astran_packages (vezi backend/server.py,
  // GooglePlayProvider/AppleProvider: product_id = package["package_id"]), deci produsele
  // trebuie create in Play Console / App Store Connect cu exact aceste id-uri.
  useEffect(() => {
    if (Platform.OS === "web" || !connected || packages.length === 0) return;
    fetchProducts({ skus: packages.map(p => p.package_id), type: "in-app" }).catch((e: any) => {
      console.log("[wallet] fetchProducts failed", e);
    });
  }, [connected, packages]);

  function storeProductFor(pkg: any): any {
    return (products || []).find((p: any) => p.id === pkg.package_id || p.productId === pkg.package_id);
  }

  async function buy(pkg: any) {
    if (Platform.OS === "web") {
      setErr("Purchases are not available on web yet - use the mobile app.");
      return;
    }
    if (!connected) {
      setErr("Store connection not ready, try again in a moment.");
      return;
    }
    setBusy(true); setErr(null); setPendingCreditWarning(null);
    try {
      // Formatul de request e DIFERIT pe iOS vs Android (vezi expo-iap) - trimitem
      // amandoua chei, biblioteca o foloseste doar pe cea a platformei curente.
      await requestPurchase({
        request: { ios: { sku: pkg.package_id }, android: { skus: [pkg.package_id] } },
        type: "in-app",
      });
      // Rezultatul (succes sau eroare) ajunge in onPurchaseSuccess/onPurchaseError de mai
      // sus (callback-uri ale useIAP), nu aici - requestPurchase doar PORNESTE fluxul
      // nativ (dialogul de plata Google/Apple).
    } catch (e: any) {
      setErr(e?.message || String(e));
      setBusy(false);
    }
  }

  // Apelat de useIAP cand Google/Apple confirma ca userul a finalizat plata in dialogul
  // nativ. De aici, pasii sunt STRICT in aceasta ordine:
  //   1. trimitem tokenul/chitanta REALA la server, pentru verificare (nu mai trimitem
  //      niciun provider_token fals, ca inainte)
  //   2. DOAR daca serverul confirma si crediteaza Astrans, inchidem tranzactia la
  //      Google/Apple (finishTransaction / consumeAsync)
  // Ordinea asta conteaza: daca am inchide tranzactia INAINTE sa stim sigur ca serverul a
  // creditat, si request-ul catre server ar pica (retea, server jos chiar atunci), userul
  // ar ramane platit dar necreditat - FARA nicio cale sa mai recupereze, pentru ca
  // Google/Apple ar considera tranzactia deja "consumata". Asa, daca pasul 1 esueaza, NU
  // inchidem tranzactia - ramane in asteptare la Google/Apple si va fi redata automat
  // (acelasi purchase, din nou prin onPurchaseSuccess) data viitoare cand se deschide acest
  // ecran, pana cand reuseste sa fie creditata.
  async function handlePurchaseSuccess(purchase: any) {
    try {
      const provider = Platform.OS === "android" ? "google_play" : "apple";
      const provider_token: string | undefined =
        Platform.OS === "android"
          ? (purchase.purchaseToken ?? purchase.purchaseTokenAndroid)
          : (purchase.transactionReceipt ?? purchase.jwsRepresentationIOS);
      const package_id: string | undefined = purchase.productId ?? purchase.id;
      if (!provider_token || !package_id) throw new Error("Incomplete purchase data from the store");

      await api("/wallet/buy", {
        method: "POST",
        body: JSON.stringify({ package_id, provider, provider_token }),
      });

      // Serverul a confirmat si a creditat Astrans (sau tranzactia era deja creditata -
      // idempotent) - DOAR ACUM e sigur sa inchidem/consumam tranzactia.
      await finishTransaction({ purchase, isConsumable: true });
      await refresh();
      await load();
    } catch (e: any) {
      console.log("[wallet] credit after purchase failed", e);
      setPendingCreditWarning(
        "Plata a fost confirmată de magazin, dar creditarea Astrans a eșuat (conexiune). " +
        "Nu e nevoie să plătești din nou - redeschide acest ecran și achiziția va fi " +
        "finalizată automat."
      );
      // NU apelam finishTransaction: tranzactia ramane needisa la Google/Apple si va
      // ajunge din nou in onPurchaseSuccess (acelasi purchase) data viitoare.
    } finally {
      setBusy(false);
    }
  }

  async function preview() {
    setErr(null); setTransferPreview(null);
    try {
      const res = await api("/wallet/transfer/preview", { method: "POST", body: JSON.stringify({ recipient_user_id: transferTo, amount: Number(transferAmt) }) });
      setTransferPreview(res);
    } catch (e: any) { setErr(e.message); }
  }

  async function doTransfer() {
    setBusy(true); setErr(null);
    try {
      await api("/wallet/transfer", { method: "POST", body: JSON.stringify({
        recipient_user_id: transferTo, amount: Number(transferAmt), idempotency_key: `${Date.now()}-${Math.random()}`,
      })});
      setShowTransfer(false); setTransferTo(""); setTransferAmt(""); setTransferPreview(null);
      await refresh(); await load();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} testID="wallet-back"><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.title}>{t("wallet")}</Text>
        <View style={{ width: 26 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 40 }}>
        <View style={styles.balanceCard}>
          <Text style={styles.balanceLabel}>{t("balance")}</Text>
          <View style={styles.balanceRow}>
            <MaterialCommunityIcons name="hexagon-slice-6" size={32} color={colors.brand} />
            <Text style={styles.balanceValue} testID="wallet-balance">{(user?.astrans_balance || 0).toLocaleString()}</Text>
          </View>
          <Text style={styles.balanceCurrency}>{t("astrans")}</Text>
          <View style={{ marginTop: 12 }}>
            <PrimaryButton testID="wallet-transfer-btn" label={t("transfer")} icon="send" onPress={() => setShowTransfer(true)} />
          </View>
        </View>

        {pendingCreditWarning ? <Text style={styles.warn} testID="wallet-pending-credit">{pendingCreditWarning}</Text> : null}

        <Text style={styles.section}>{t("buy_astrans").toUpperCase()}</Text>
        {Platform.OS === "web" ? (
          <Text style={styles.webNotice}>Purchases are available in the mobile app.</Text>
        ) : null}
        <View style={styles.pkgGrid}>
          {loading ? <ActivityIndicator color={colors.brand} /> : packages.map(p => {
            // Pretul AFISAT trebuie sa fie cel REAL, localizat, intors de Google/Apple -
            // politica ambelor magazine cere ca pretul aratat userului sa corespunda exact
            // celui din dialogul de plata. Pretul in RON din backend (p.price) e doar
            // fallback, cat timp produsele inca nu s-au incarcat de la magazin (sau pe web).
            const storeProduct = storeProductFor(p);
            const displayPrice = storeProduct?.displayPrice || storeProduct?.localizedPrice || `${p.price} ${currency}`;
            return (
              <Pressable key={p.package_id} testID={`buy-${p.package_id}`} onPress={() => buy(p)} style={styles.pkg} disabled={busy}>
                <MaterialCommunityIcons name="hexagon-slice-6" size={28} color={colors.brand} />
                <Text style={styles.pkgAmt}>{p.astrans}</Text>
                <Text style={styles.pkgLabel}>{p.label}</Text>
                <View style={styles.pkgPriceBox}>
                  <Text style={styles.pkgPrice}>{displayPrice}</Text>
                </View>
              </Pressable>
            );
          })}
        </View>

        {err ? <Text style={styles.err} testID="wallet-error">{err}</Text> : null}

        <Text style={styles.section}>{t("transactions").toUpperCase()}</Text>
        {transactions.map(tx => (
          <View key={tx.tx_id} style={styles.tx}>
            <MaterialCommunityIcons
              name={tx.type === "purchase" ? "cart" : tx.type === "transfer_out" ? "send" : tx.type === "transfer_in" ? "download" : "gift"}
              size={20}
              color={tx.type === "transfer_out" ? colors.error : colors.brand}
            />
            <View style={{ flex: 1 }}>
              <Text style={styles.txTitle}>{tx.reference || tx.type}</Text>
              <Text style={styles.txMeta}>{new Date(tx.timestamp).toLocaleString()}</Text>
            </View>
            <Text style={[styles.txAmt, { color: tx.type === "transfer_out" ? colors.error : colors.brand }]}>
              {tx.type === "transfer_out" ? "-" : "+"}{tx.amount}
            </Text>
          </View>
        ))}
      </ScrollView>

      <Modal visible={showTransfer} animationType="slide" transparent onRequestClose={() => setShowTransfer(false)}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1, justifyContent: "flex-end" }}>
          <Pressable onPress={() => setShowTransfer(false)} style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.6)" }} />
          <ScrollView style={styles.sheet} contentContainerStyle={{ paddingBottom: 30 }} keyboardShouldPersistTaps="handled">
            <Text style={styles.sheetTitle}>{t("transfer")}</Text>
            <Text style={styles.label}>Recipient user_id</Text>
            <TextInput testID="transfer-recipient" value={transferTo} onChangeText={setTransferTo} placeholderTextColor={colors.onSurface3} style={styles.input} placeholder="user_..." />
            <Text style={styles.label}>{t("astrans")}</Text>
            <TextInput testID="transfer-amount" value={transferAmt} onChangeText={setTransferAmt} keyboardType="number-pad" placeholderTextColor={colors.onSurface3} style={styles.input} placeholder="100" />
            {transferPreview ? (
              <View style={styles.previewBox}>
                <Text style={styles.previewLine}>{t("send")}: {transferPreview.gross}</Text>
                <Text style={styles.previewLine}>{t("fee")}: {transferPreview.fee}</Text>
                <Text style={styles.previewLine}>{t("receive")}: {transferPreview.net}</Text>
              </View>
            ) : null}
            {err ? <Text style={styles.err}>{err}</Text> : null}
            <View style={{ flexDirection: "row", gap: 8, marginTop: 16 }}>
              <View style={{ flex: 1 }}>
                <PrimaryButton testID="transfer-preview-btn" label="Preview" onPress={preview} disabled={!transferTo || !transferAmt} />
              </View>
              <View style={{ flex: 1 }}>
                <PrimaryButton testID="transfer-confirm-btn" label={busy ? "..." : t("send")} onPress={doTransfer} disabled={!transferPreview || busy} />
              </View>
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.lg },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  balanceCard: { alignItems: "center", padding: spacing.xl, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border },
  balanceLabel: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 2 },
  balanceRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 8 },
  balanceValue: { color: colors.onSurface, fontSize: 44, fontWeight: "900", letterSpacing: -1 },
  balanceCurrency: { color: colors.brand, fontSize: 11, fontWeight: "800", letterSpacing: 3, marginTop: 4 },
  section: { color: colors.onSurface, fontSize: 13, fontWeight: "800", letterSpacing: 1.5, marginTop: 28, marginBottom: 10 },
  webNotice: { color: colors.onSurface3, fontSize: 12, marginBottom: 10 },
  pkgGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  pkg: { width: "48%", padding: 16, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  pkgAmt: { color: colors.onSurface, fontSize: 22, fontWeight: "900", marginTop: 6 },
  pkgLabel: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  pkgPriceBox: { marginTop: 8, backgroundColor: colors.brand, paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill },
  pkgPrice: { color: colors.onBrand, fontWeight: "900", fontSize: 12 },
  tx: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  txTitle: { color: colors.onSurface, fontWeight: "700", fontSize: 13 },
  txMeta: { color: colors.onSurface3, fontSize: 11, marginTop: 2 },
  txAmt: { fontWeight: "900", fontSize: 14 },
  sheet: { backgroundColor: colors.surface, padding: spacing.lg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, borderTopWidth: 1, borderColor: colors.border, paddingBottom: 30 },
  sheetTitle: { color: colors.onSurface, fontSize: 20, fontWeight: "900", marginBottom: 12 },
  label: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 12 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, borderWidth: 1, borderColor: colors.border },
  previewBox: { marginTop: 16, padding: 14, backgroundColor: colors.brandTint, borderRadius: radius.md, borderWidth: 1, borderColor: colors.brand },
  previewLine: { color: colors.brand, fontSize: 13, fontWeight: "700", marginBottom: 4 },
  err: { color: colors.error, marginTop: 10, fontSize: 12, fontWeight: "600" },
  warn: { color: colors.brand, marginTop: 12, fontSize: 12, fontWeight: "700", backgroundColor: colors.brandTint, padding: 10, borderRadius: radius.md },
});