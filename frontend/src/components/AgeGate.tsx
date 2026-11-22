import { useEffect } from "react";
import { usePathname, useRouter } from "expo-router";
import { useAuth } from "../context/AuthContext";

// Rute pe care un cont cu pasi de varsta/acord nefinalizati are voie sa le vada (aliniat cu
// age_policy.RESTRICTED_ALLOWED_PREFIXES din backend; backend-ul ramane SURSA de adevar - asta e doar UX).
const FLOW_PATHS = ["/age", "/parental-wait", "/age-verify", "/parental", "/login", "/register", "/forgot", "/legal", "/settings"];

/** Redirectioneaza conturile care mai au de facut pasi de varsta (data nasterii lipsa, acord parental). */
export default function AgeGate() {
  const { user } = useAuth();
  const path = usePathname() || "/";
  const router = useRouter();
  useEffect(() => {
    if (!user) return;
    if (FLOW_PATHS.some((p) => path === p || path.startsWith(p + "/"))) return;
    if (user.needs_age_info) { router.replace("/(onboarding)/age"); return; }
    if (user.restricted) router.replace("/(onboarding)/parental-wait");
  }, [user, path, router]);
  return null;
}
