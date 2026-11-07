// Avatar de prieten in mesagerie: aceleasi culori/forma ca in lista de prieteni (cerc cu initiala),
// cu poza de profil daca exista. Punctul verde apare doar daca `online` e adevarat (backend-ul nu
// are inca un sistem de prezenta, deci acum nu apare niciodata - la fel ca in lista de prieteni).
import React, { useState } from "react";
import { Image, StyleSheet, Text, View } from "react-native";
import { colors } from "@/src/theme";
import type { DmFriend } from "./dmStore";

export function DmAvatar({ friend, size = 44 }: { friend: Pick<DmFriend, "display_name" | "username" | "avatar_url" | "online">; size?: number }) {
  const [broken, setBroken] = useState(false);
  const label = (friend.display_name || friend.username || "?").trim();
  const url = typeof friend.avatar_url === "string" && /^https?:\/\//i.test(friend.avatar_url) ? friend.avatar_url : null;
  const dot = Math.max(8, Math.round(size / 4));
  return (
    <View style={[styles.avatar, { width: size, height: size, borderRadius: size / 2 }]}>
      {url && !broken ? (
        <Image source={{ uri: url }} style={{ width: size, height: size, borderRadius: size / 2 }} onError={() => setBroken(true)} />
      ) : (
        <Text style={[styles.letter, { fontSize: Math.round(size * 0.4) }]}>{(label[0] || "?").toUpperCase()}</Text>
      )}
      {friend.online ? <View style={[styles.dot, { width: dot, height: dot, borderRadius: dot / 2 }]} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: { backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center", position: "relative" },
  letter: { color: colors.brand, fontWeight: "900" },
  dot: { position: "absolute", right: 0, bottom: 0, backgroundColor: colors.success, borderWidth: 2, borderColor: colors.surface },
}); 