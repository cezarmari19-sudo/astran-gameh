// Shell responsiv pentru modalul de Settings din Play Mode. Contine DOAR layout/scroll:
// continutul setarilor (Viziune, Grafica, Render, Shift Lock) ramane in ecranele Play si
// in usePlayerSettings - nimic din logica sau valorile setarilor nu e duplicat aici.
//
// Structura: titlu fix (sus) / zona scrollabila (mijloc, flexShrink + minHeight:0) /
// butonul Close fix (jos). Inaltimea maxima se calculeaza din viewport-ul curent minus
// safe-area, deci se adapteaza la orice ecran/orientare, fara dimensiuni fixe.
import React from "react";
import { View, Text, StyleSheet, Pressable, Modal, ScrollView, useWindowDimensions } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors, radius, spacing } from "@/src/theme";

type Props = {
  visible: boolean;
  onClose: () => void;
  title?: string;
  closeLabel?: string;
  children: React.ReactNode;
};

export default function SettingsModalShell({ visible, onClose, title = "Settings", closeLabel = "Close", children }: Props) {
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const isLandscape = width > height;

  const padTop = Math.max(insets.top, spacing.md);
  const padBottom = Math.max(insets.bottom, spacing.md);
  const padLeft = Math.max(insets.left, spacing.md);
  const padRight = Math.max(insets.right, spacing.md);
  const maxBoxHeight = Math.max(160, height - padTop - padBottom);

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      statusBarTranslucent
      supportedOrientations={["portrait", "portrait-upside-down", "landscape", "landscape-left", "landscape-right"]}
      onRequestClose={onClose}
    >
      <View style={[styles.backdrop, { paddingTop: padTop, paddingBottom: padBottom, paddingLeft: padLeft, paddingRight: padRight }]}>
        {/* Backdrop-ul care inchide modalul e frate (nu parinte) al casetei: un Pressable parinte
            inghite gesturile de scroll ale ScrollView-ului copil pe mobil. */}
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} accessibilityLabel="Close settings" />
        <View style={[styles.box, isLandscape && styles.boxLandscape, { maxHeight: maxBoxHeight }]}>
          <Text style={styles.title}>{title}</Text>
          <ScrollView
            style={styles.scroll}
            contentContainerStyle={styles.scrollContent}
            showsVerticalScrollIndicator
            keyboardShouldPersistTaps="handled"
            nestedScrollEnabled
            bounces={false}
          >
            {children}
          </ScrollView>
          <Pressable testID="play-settings-close" onPress={onClose} style={styles.closeBtn}>
            <Text style={styles.closeBtnText}>{closeLabel}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center" },
  box: { width: "100%", maxWidth: 340, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  boxLandscape: { maxWidth: 420 },
  title: { color: colors.onSurface, fontSize: 17, fontWeight: "900", marginBottom: 6 },
  scroll: { flexShrink: 1, flexGrow: 0, minHeight: 0 },
  scrollContent: { paddingBottom: spacing.xs },
  closeBtn: { marginTop: spacing.md, backgroundColor: colors.brand, borderRadius: radius.pill, paddingVertical: 12, alignItems: "center" },
  closeBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
});