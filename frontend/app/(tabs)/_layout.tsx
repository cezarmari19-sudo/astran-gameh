import React from "react";
import { Tabs } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors } from "@/src/theme";
import { useI18n } from "@/src/i18n";

export default function TabsLayout() {
  const { t } = useI18n();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brand,
        tabBarInactiveTintColor: colors.onSurface3,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          borderTopWidth: 1,
          height: 68,
          paddingBottom: 10,
          paddingTop: 8,
        },
        tabBarLabelStyle: { fontSize: 11, fontWeight: "700", letterSpacing: 0.5 },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: t("discover"),
          tabBarIcon: ({ color }) => <MaterialCommunityIcons name="compass" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="studio"
        options={{
          title: t("studio"),
          tabBarIcon: ({ color }) => <MaterialCommunityIcons name="hammer-wrench" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="avatar"
        options={{
          title: "Avatar",
          tabBarIcon: ({ color }) => <MaterialCommunityIcons name="account-outline" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="friends"
        options={{
          title: t("friends"),
          tabBarIcon: ({ color }) => <MaterialCommunityIcons name="account-group" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: t("profile"),
          tabBarIcon: ({ color }) => <MaterialCommunityIcons name="account-circle" size={22} color={color} />,
        }}
      />
    </Tabs>
  );
}