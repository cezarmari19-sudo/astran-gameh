import { useFonts } from "expo-font";
import MaterialCommunityIcons from "@expo/vector-icons/build/vendor/react-native-vector-icons/Fonts/MaterialCommunityIcons.ttf";

export function useIconFonts() {
  return useFonts({
    MaterialCommunityIcons,
  });
}