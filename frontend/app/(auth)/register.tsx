import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, KeyboardAvoidingView, Platform, ScrollView, Pressable, Modal } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

// Textul complet al Politicii de Confidentialitate Astran Game (v1.0). Placeholder-ele
// dintre paranteze drepte raman neschimbate pana cand sunt furnizate valorile reale.
const PRIVACY_POLICY_TEXT = `ASTRAN GAME — POLITICA DE CONFIDENȚIALITATE

Versiune: 1.0
Data intrării în vigoare: [DATA — ÎNLOCUIEȘTE CÂND ESTE DISPONIBILĂ]
Operator: [NUMELE LEGAL AL COMPANIEI — ÎNLOCUIEȘTE CÂND ÎL AI]
E-mail pentru confidențialitate: [EMAIL — ÎNLOCUIEȘTE CÂND ÎL AI]
Website: [WEBSITE — ÎNLOCUIEȘTE CÂND ÎL AI]


1. DESPRE ACEASTĂ POLITICĂ DE CONFIDENȚIALITATE

Astran Game („Astran”, „noi”, „nouă” sau „platforma”) este o platformă destinată creării, publicării, descoperirii și utilizării jocurilor și a altui conținut creat de utilizatori.

Această Politică de Confidențialitate explică:

- ce informații putem colecta;
- cum putem utiliza informațiile;
- cum putem analiza informațiile;
- cum putem utiliza informațiile pentru moderare și securitate;
- cum putem utiliza informațiile pentru dezvoltarea și antrenarea sistemelor AI;
- cum putem utiliza informațiile pentru publicitate și personalizare;
- cum putem transmite informațiile către furnizori și parteneri;
- când și în ce condiții putem comercializa, licenția sau transmite anumite date către terți;
- unde pot fi procesate datele;
- ce se poate întâmpla cu datele după ștergerea unui cont.

Astran poate opera la nivel internațional, iar datele utilizatorilor pot fi procesate în țări diferite de țara în care utilizatorul locuiește.


2. CATEGORIILE DE UTILIZATORI

La crearea contului, utilizatorul trebuie să selecteze una dintre următoarele categorii:


2.1. SUB 18 ANI

Această categorie este destinată persoanelor cu vârsta între 0 și 17 ani.

În funcție de țara utilizatorului și de legislația aplicabilă, Astran poate solicita acordul unui părinte sau tutore înainte ca utilizatorul să poată utiliza anumite funcții sau platforma.

Atunci când este necesar acordul parental, Astran poate solicita adresa de e-mail a părintelui sau tutorelui și poate procesa informațiile necesare pentru verificarea și înregistrarea acordului parental.

Astran poate aplica restricții suplimentare anumitor categorii de vârstă în cadrul grupului de utilizatori sub 18 ani, în funcție de legislația aplicabilă, de funcțiile platformei și de măsurile de siguranță implementate.


2.2. 18+

Această categorie este destinată persoanelor care au împlinit cel puțin 18 ani.

Astran poate aplica reguli, funcții, restricții și mecanisme de verificare diferite în funcție de categoria de vârstă și de legislația aplicabilă.

Selectarea categoriei de vârstă nu înlocuiește verificările sau consimțământul parental care pot fi necesare conform legislației aplicabile.


3. DATELE PE CARE LE PUTEM COLECTA

În funcție de modul în care este utilizată platforma și de funcțiile disponibile, Astran poate colecta următoarele categorii de informații.


3.1. DATE DESPRE CONT

Putem colecta:

- adresa de e-mail;
- numele de utilizator;
- numele afișat;
- parola;
- categoria de vârstă;
- data nașterii sau alte informații privind vârsta, atunci când sunt necesare;
- ID-ul contului;
- data creării contului;
- setările contului;
- preferințele contului.

Parolele nu sunt destinate stocării în format text simplu și trebuie protejate prin metode criptografice adecvate, cum ar fi hashing-ul cu un algoritm modern.

Parolele nu vor fi comercializate sau furnizate unor terți în forma lor originală.


4. DATE DESPRE DISPOZITIV ȘI CONEXIUNE

Astran poate colecta:

- adresa IP;
- tipul dispozitivului;
- producătorul și modelul dispozitivului;
- versiunea sistemului de operare;
- versiunea aplicației;
- identificatori ai dispozitivului;
- identificatori ai aplicației;
- informații despre rețea;
- informații despre browser, dacă platforma este accesată prin browser;
- informații despre performanță;
- crash-uri;
- erori;
- log-uri tehnice;
- evenimente de securitate;
- informații necesare pentru prevenirea fraudelor și abuzurilor.

Astran nu colectează în mod intenționat locația aproximativă a utilizatorului, cu excepția cazului în care acest lucru este modificat ulterior și este comunicat corespunzător utilizatorilor.


5. DATE DESPRE AVATAR ȘI IDENTITATEA DIN JOC

Astran poate colecta și procesa:

- avatarul utilizatorului;
- hainele avatarului;
- accesorii;
- modele de personaje;
- animații;
- modificări ale avatarului;
- obiecte deținute;
- obiecte create;
- obiecte publicate;
- configurația avatarului;
- istoricul modificărilor;
- identificatorii obiectelor asociate contului.

Aceste date pot fi utilizate pentru funcționarea platformei, afișarea profilului și a avatarului, multiplayer, personalizare, moderare, analiză, dezvoltarea produsului și alte scopuri descrise în această politică.


6. DATE DESPRE JOCURI ȘI ACTIVITATEA UTILIZATORULUI

Atunci când utilizatorul folosește Astran Game, putem colecta informații despre activitatea sa.

Acestea pot include:

- jocurile accesate;
- jocurile jucate;
- durata sesiunilor;
- timpul petrecut în fiecare joc;
- acțiunile efectuate în joc;
- mișcările personajului;
- poziția personajului în lumea jocului;
- direcția de deplasare;
- viteza;
- săriturile;
- interacțiunile cu obiectele;
- obiectele utilizate;
- obiectele colectate;
- evenimentele din joc;
- istoricul jocurilor;
- interacțiunile multiplayer;
- acțiunile efectuate în Studio;
- acțiunile efectuate în editor;
- jocurile create;
- modificările efectuate asupra jocurilor;
- modelele create;
- avatarurile create;
- conținutul publicat;
- conținutul șters;
- informații privind performanța jocurilor.

Astran poate decide ca anumite categorii de date să fie păstrate pentru o anumită perioadă, anonimizate, agregate, șterse sau să nu fie păstrate deloc, în funcție de necesitățile platformei, funcției și cerințele legale.


7. CHAT TEXT

Astran poate procesa mesajele și alte comunicări text realizate prin platformă.

Acestea pot include:

- chat-ul din joc;
- chat-ul public;
- mesaje private;
- mesaje între prieteni;
- comunicări între utilizatori;
- conținut transmis prin funcțiile sociale ale platformei.

Mesajele pot fi:

- analizate automat;
- analizate de sisteme AI;
- moderate;
- raportate;
- stocate;
- păstrate pentru perioade diferite;
- șterse;
- anonimizate;
- utilizate pentru securitate;
- utilizate pentru îmbunătățirea serviciilor;
- utilizate pentru cercetare și dezvoltare;
- utilizate pentru dezvoltarea și antrenarea sistemelor AI, atunci când există un temei legal corespunzător;
- utilizate în alte scopuri permise de legislația aplicabilă.

În funcție de funcție și de necesitățile platformei, anumite conversații pot să nu fie păstrate deloc, în timp ce altele pot fi păstrate pentru o perioadă mai lungă.


8. VOICE CHAT ȘI DATE AUDIO

Dacă utilizatorul acordă aplicației permisiunea de a accesa microfonul și utilizează o funcție care necesită microfonul, Astran poate procesa date audio și voce.

Acestea pot include:

- vocea utilizatorului;
- înregistrări audio;
- transmisii audio;
- conversații vocale;
- metadate asociate comunicării vocale;
- informații tehnice despre transmisia audio.

În funcție de funcție, setări și necesități, anumite comunicații audio pot:

- să nu fie înregistrate;
- să fie procesate temporar;
- să fie păstrate;
- să fie șterse după o anumită perioadă;
- să fie analizate pentru moderare;
- să fie utilizate pentru securitate;
- să fie utilizate pentru dezvoltarea serviciului;
- să fie utilizate pentru dezvoltarea sau antrenarea sistemelor AI, atunci când acest lucru este permis de lege și de baza juridică aplicabilă;
- să fie utilizate în alte scopuri permise de legislația aplicabilă.

Dacă utilizatorul nu acordă permisiunea sistemului de operare pentru microfon, Astran nu poate accesa microfonul prin funcția respectivă.


9. CAMERĂ ȘI DATE VIDEO

Dacă Astran introduce în viitor funcții care necesită acces la cameră, aplicația poate solicita permisiunea sistemului de operare.

Dacă utilizatorul acordă permisiunea și utilizează funcția respectivă, Astran poate procesa:

- imagini;
- video;
- transmisii video;
- înregistrări;
- informații tehnice asociate;
- conținut generat prin cameră.

În funcție de funcție, aceste date pot fi procesate temporar, înregistrate, păstrate, analizate, moderate, utilizate pentru dezvoltarea serviciului sau utilizate în alte scopuri permise de legislația aplicabilă.

Fără permisiunea necesară a sistemului de operare, Astran nu poate accesa camera prin funcția respectivă.


10. PRIETENI ȘI INTERACȚIUNI SOCIALE

Putem colecta informații despre:

- lista de prieteni;
- cereri de prietenie;
- utilizatori blocați;
- utilizatori raportați;
- utilizatori dezactivați sau mutați;
- interacțiuni între utilizatori;
- grupuri;
- roluri;
- administratori;
- membri;
- activitatea socială asociată contului.


11. RAPOARTE, BLOCĂRI ȘI MODERARE

Dacă un utilizator raportează, blochează sau dezactivează un alt utilizator, putem colecta și păstra informațiile necesare pentru:

- investigarea raportului;
- aplicarea regulilor;
- prevenirea abuzului;
- securitate;
- detectarea fraudelor;
- soluționarea disputelor;
- aplicarea sancțiunilor;
- protejarea utilizatorilor.


12. ACHIZIȚII ȘI INFORMAȚII FINANCIARE

Astran poate colecta:

- istoricul achizițiilor;
- produsele cumpărate;
- monedele virtuale cumpărate;
- abonamente;
- tranzacții;
- rambursări;
- informații despre metoda de plată;
- identificatori de tranzacție;
- informații furnizate de procesatorii de plăți.

Datele complete ale cardului sau alte date financiare sensibile pot fi procesate de procesatori de plăți specializați, în funcție de metoda de plată utilizată.

Astran nu intenționează să comercializeze date precum numărul complet al cardului sau parolele.

În schimb, putem transmite informațiile necesare procesatorilor de plăți și altor furnizori autorizați.


13. DATE FURNIZATE DE PĂRINȚI SAU TUTORI

Dacă este necesar consimțământul parental, Astran poate colecta de la părintele sau tutorele:

- adresa de e-mail;
- numele, dacă este necesar;
- informații necesare verificării consimțământului;
- data și momentul acordării consimțământului;
- informații privind relația cu minorul, atunci când este necesar;
- informații tehnice și administrative asociate procesului de verificare.

Aceste date pot fi utilizate pentru verificarea, documentarea și gestionarea consimțământului parental.


14. CUM PUTEM UTILIZA DATELE

În măsura permisă de legislația aplicabilă și în funcție de baza juridică relevantă, Astran poate utiliza datele pentru:

- furnizarea platformei;
- funcționarea jocurilor;
- multiplayer;
- conturi;
- autentificare;
- securitate;
- prevenirea fraudelor;
- prevenirea abuzului;
- moderare;
- aplicarea regulilor;
- suport;
- rezolvarea problemelor;
- dezvoltarea produsului;
- analiză;
- statistici;
- cercetare;
- personalizare;
- recomandări;
- publicitate;
- marketing;
- măsurarea performanței;
- dezvoltarea de noi funcții;
- dezvoltarea și antrenarea sistemelor AI;
- evaluarea sistemelor AI;
- îmbunătățirea sistemelor AI;
- detectarea conținutului problematic;
- detectarea comportamentului automatizat sau fraudulos;
- protejarea platformei;
- respectarea obligațiilor legale;
- alte scopuri permise de legislația aplicabilă.


15. UTILIZAREA PENTRU AI

Astran poate utiliza anumite date colectate prin platformă pentru:

- dezvoltarea modelelor AI;
- antrenarea modelelor AI;
- evaluarea modelelor AI;
- testarea modelelor AI;
- îmbunătățirea modelelor AI;
- cercetare;
- dezvoltarea unor produse sau servicii bazate pe AI.

Aceste date pot include, în funcție de funcție și de temeiul juridic aplicabil:

- date despre jocuri;
- comportament în joc;
- conversații text;
- interacțiuni;
- conținut creat;
- date audio;
- voce;
- alte date tehnice.

Astran poate utiliza datele direct sau prin furnizori, parteneri, cercetători sau alte organizații autorizate.


16. RESTRICȚIE PRIVIND ANUMITE UTILIZĂRI AI ALE VOCIi

Deși Astran poate permite utilizarea anumitor date pentru dezvoltarea sistemelor AI, Astran nu autorizează utilizarea datelor vocale, a stilului vocal sau a altor date asociate vocii pentru dezvoltarea unor sisteme AI destinate în mod specific:

- generării de conținut pornografic;
- generării de deepfake-uri sexuale;
- simulării sexuale a unei persoane;
- imitării sexuale neconsensuale a unei persoane;
- creării unei simulări romantice sau intime prezentate ca o relație cu utilizatorul;
- altor utilizări similare pe care Astran le interzice prin contract sau politică.

Această restricție poate fi inclusă în acordurile contractuale cu destinatarii datelor pentru a impune respectarea ei.


17. COMERCIALIZAREA ȘI LICENȚIEREA DATELOR

În măsura permisă de lege și în baza juridică aplicabilă, Astran poate licenția, transmite, pune la dispoziție sau comercializa anumite date și informații către terți.

Acești terți pot include:

- companii;
- furnizori;
- parteneri comerciali;
- cercetători;
- dezvoltatori;
- organizații;
- instituții;
- alte entități;
- în anumite situații, persoane fizice.

Datele pot fi furnizate pentru scopuri precum:

- analiză;
- cercetare;
- publicitate;
- marketing;
- personalizare;
- AI;
- dezvoltare software;
- securitate;
- statistică;
- cercetare comercială;
- dezvoltarea de produse;
- servicii comerciale;
- alte scopuri permise de legislația aplicabilă.


18. TRANSMITEREA ULTERIOARĂ DE CĂTRE DESTINATARI

În măsura permisă de lege și de acordurile aplicabile, un destinatar al datelor poate utiliza datele primite și le poate transmite sau licenția mai departe.

Astfel, datele pot circula între mai multe organizații sau persoane.

Acolo unde Astran impune contractual restricții privind anumite utilizări, destinatarii trebuie să respecte respectivele restricții.

Astran nu poate garanta modul în care un terț va utiliza ulterior datele după ce transferul a fost efectuat, dacă legea și contractele aplicabile permit acea utilizare.


19. DATE SENSIBILE ȘI DATE CU RISC RIDICAT

Astran nu intenționează să comercializeze în mod obișnuit anumite date extrem de sensibile, cum ar fi:

- parole;
- numere complete de card;
- chei de autentificare;
- token-uri secrete;
- alte credențiale de securitate.

Pentru alte categorii de date personale, posibilitatea de transmitere, licențiere sau comercializare va depinde de legislația aplicabilă, baza juridică și consimțământul necesar.


20. PROCESAREA INTERNAȚIONALĂ

Astran poate utiliza furnizori și infrastructură aflate în:

- Europa;
- America de Nord;
- America de Sud;
- Asia;
- Africa;
- Australia și Oceania;
- alte regiuni.

Prin urmare, datele pot fi transferate sau procesate pe alte continente decât cel în care locuiește utilizatorul.

Pentru transferurile internaționale, Astran va aplica mecanismele juridice necesare atunci când acestea sunt cerute de legislația aplicabilă.


21. CÂT TIMP PĂSTRĂM DATELE

Astran poate păstra diferite categorii de date pentru perioade diferite.

Unele date pot fi:

- șterse rapid;
- păstrate temporar;
- păstrate atât timp cât sunt necesare funcției;
- păstrate pentru securitate;
- păstrate pentru obligații legale;
- anonimizate;
- agregate;
- păstrate în sisteme de backup pentru o anumită perioadă.

Perioada de păstrare poate varia în funcție de tipul datelor, scopul pentru care au fost colectate, funcția utilizată, obligațiile legale și alte circumstanțe relevante.

Astran nu garantează că toate datele sunt șterse imediat după încetarea utilizării unei funcții.


22. CE SE ÎNTÂMPLĂ CÂND ȘTERGI CONTUL

Ștergerea contului nu înseamnă automat că fiecare copie a fiecărei informații asociate contului este imediat eliminată din toate sistemele sau de la toți destinatarii.

Detaliile privind acest lucru vor fi prezentate într-o Politică separată privind ștergerea contului și păstrarea datelor.

În funcție de legislația aplicabilă, anumite date pot rămâne necesare pentru:

- obligații legale;
- securitate;
- prevenirea fraudelor;
- soluționarea disputelor;
- evidențe financiare;
- backup-uri;
- respectarea obligațiilor contractuale;
- date deja anonimizate sau agregate;
- date care au fost deja transmise unui terț în condițiile permise de lege.

Ștergerea contului oprește utilizarea contului ca serviciu activ și, în funcție de funcție și de legislația aplicabilă, poate opri colectarea unor date noi asociate contului.

Datele colectate anterior pot continua să existe în sistemele Astran sau în sistemele terților atunci când acest lucru este permis de lege și de acordurile aplicabile.

Drepturile obligatorii de ștergere și alte drepturi prevăzute de legislația aplicabilă vor fi respectate.


23. MODIFICAREA ACESTEI POLITICI

Astran poate modifica această Politică de Confidențialitate.

Dacă modificările sunt importante, Astran poate solicita utilizatorului să revizuiască și, atunci când este necesar, să accepte noua versiune înainte de a continua utilizarea anumitor servicii.

Pentru prelucrările pentru care este necesar un consimțământ nou sau separat, Astran va solicita consimțământul corespunzător.


24. UTILIZATORII MINORI

Astran aplică măsuri suplimentare atunci când utilizatorul este minor.

Acestea pot include:

- verificarea vârstei;
- solicitarea e-mailului unui părinte sau tutore;
- obținerea consimțământului parental;
- limitarea anumitor funcții;
- limitarea anumitor tipuri de publicitate;
- limitarea anumitor tipuri de transmitere a datelor;
- mecanisme suplimentare de siguranță.

Pentru utilizatorii copii, anumite forme de comercializare sau divulgare a datelor pot necesita consimțământ parental separat sau pot fi restricționate de lege.

Regulile aplicabile minorilor pot varia în funcție de țara în care utilizatorul se află.


25. RESPECTAREA LEGISLAȚIEI

Nicio prevedere din această Politică de Confidențialitate nu trebuie interpretată ca permițând Astran să efectueze o prelucrare care este interzisă de legislația aplicabilă.

Dacă o lege locală acordă utilizatorului protecții suplimentare, Astran va respecta aceste cerințe în măsura în care sunt aplicabile.

În special, pentru utilizatorii din UE și SEE, Astran va ține cont de cerințele legislației privind protecția datelor, inclusiv principiile privind legalitatea, transparența, limitarea scopului, minimizarea datelor, securitatea și limitarea stocării.


26. CONTACT

Pentru întrebări privind această Politică de Confidențialitate:

Operator:
[NUMELE LEGAL AL COMPANIEI — ÎNLOCUIEȘTE CÂND ÎL AI]

E-mail:
[EMAIL DE CONFIDENȚIALITATE — ÎNLOCUIEȘTE CÂND ÎL AI]

Website:
[WEBSITE — ÎNLOCUIEȘTE CÂND ÎL AI]

Adresă:
[ADRESA LEGALĂ — ÎNLOCUIEȘTE CÂND ESTE DISPONIBILĂ]


SFÂRȘITUL POLITICII DE CONFIDENȚIALITATE`;

export default function RegisterScreen() {
  const router = useRouter();
  const { register } = useAuth();
  const { t, lang } = useI18n();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ageCategory, setAgeCategory] = useState<"under_18" | "adult_18">("under_18");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [showPrivacy, setShowPrivacy] = useState(false);

  async function doRegister() {
    setBusy(true); setErr(null);
    try {
      await register({ email: email.trim(), password, username: username.trim(), age_category: ageCategory, language: lang });
      router.replace("/(onboarding)/age");
    } catch (e: any) {
      setErr(e.message || "Register failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.root}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Pressable onPress={() => router.back()} style={styles.back} testID="register-back">
            <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
          </Pressable>
          <Text style={styles.title}>{t("register")}</Text>
          <Text style={styles.sub}>Astran Game</Text>

          <Text style={styles.label}>{t("email")}</Text>
          <TextInput testID="register-email-input" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholderTextColor={colors.onSurface3} placeholder="you@astran.game" style={styles.input} />

          <Text style={styles.label}>{t("username")}</Text>
          <TextInput testID="register-username-input" value={username} onChangeText={setUsername} autoCapitalize="none" placeholderTextColor={colors.onSurface3} placeholder="astronaut42" style={styles.input} />

          <Text style={styles.label}>{t("password")}</Text>
          <TextInput testID="register-password-input" value={password} onChangeText={setPassword} secureTextEntry placeholderTextColor={colors.onSurface3} placeholder="••••••••" style={styles.input} />

          <Text style={[styles.label, { marginTop: 20 }]}>{t("age_select_title")}</Text>
          <View style={styles.ageRow}>
            <Pressable
              testID="register-age-under-18"
              onPress={() => setAgeCategory("under_18")}
              style={[styles.ageBtn, ageCategory === "under_18" && styles.ageBtnActive]}
            >
              <Text style={[styles.ageBtnText, ageCategory === "under_18" && styles.ageBtnTextActive]}>{t("age_under_18")}</Text>
            </Pressable>
            <Pressable
              testID="register-age-18-plus"
              onPress={() => setAgeCategory("adult_18")}
              style={[styles.ageBtn, ageCategory === "adult_18" && styles.ageBtnActive]}
            >
              <Text style={[styles.ageBtnText, ageCategory === "adult_18" && styles.ageBtnTextActive]}>{t("age_18_plus")}</Text>
            </Pressable>
          </View>

          <View style={styles.privacyRow}>
            <Pressable
              testID="register-privacy-checkbox"
              onPress={() => setPrivacyAccepted(v => !v)}
              hitSlop={8}
              style={styles.checkbox}
            >
              <MaterialCommunityIcons
                name={privacyAccepted ? "checkbox-marked" : "checkbox-blank-outline"}
                size={18}
                color={privacyAccepted ? colors.brand : colors.onSurface3}
              />
            </Pressable>
            <Text style={styles.privacyText}>
              Am citit și accept{" "}
              <Text testID="register-privacy-link" style={styles.privacyLink} onPress={() => setShowPrivacy(true)}>
                Politica de Confidențialitate
              </Text>
            </Text>
          </View>

          {err ? <Text style={styles.err} testID="register-error">{err}</Text> : null}
          <View style={{ marginTop: 24 }}>
            <PrimaryButton
              testID="register-submit-button"
              label={busy ? "..." : t("continue")}
              onPress={doRegister}
              disabled={busy || !email || !username || !password || !privacyAccepted}
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>

      <Modal
        visible={showPrivacy}
        animationType="slide"
        presentationStyle="fullScreen"
        onRequestClose={() => setShowPrivacy(false)}
      >
        <SafeAreaView style={styles.privacyRoot} edges={["top", "left", "right", "bottom"]}>
          <View style={styles.privacyHeader}>
            <Pressable testID="register-privacy-close" onPress={() => setShowPrivacy(false)} style={styles.privacyBack} hitSlop={8}>
              <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
            </Pressable>
            <Text style={styles.privacyTitle}>Politica de Confidențialitate</Text>
            <View style={styles.privacyHeaderSpacer} />
          </View>
          <ScrollView contentContainerStyle={styles.privacyContent} showsVerticalScrollIndicator>
            <Text style={styles.privacyBody}>{PRIVACY_POLICY_TEXT}</Text>
          </ScrollView>
        </SafeAreaView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.lg, paddingTop: 60, paddingBottom: 40 },
  back: { width: 40, height: 40, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  title: { color: colors.onSurface, fontSize: 30, fontWeight: "900", letterSpacing: -0.5 },
  sub: { color: colors.brand, fontSize: 12, fontWeight: "800", letterSpacing: 3, marginBottom: 24 },
  label: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 12 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, borderWidth: 1, borderColor: colors.border },
  ageRow: { flexDirection: "row", gap: 12, marginTop: 10 },
  ageBtn: { flex: 1, paddingVertical: 14, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  ageBtnActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  ageBtnText: { color: colors.onSurface2, fontWeight: "700", fontSize: 14 },
  ageBtnTextActive: { color: colors.brand },
  err: { color: colors.error, marginTop: 12, fontSize: 12, fontWeight: "600" },
  privacyRow: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: 16 },
  checkbox: { width: 18, height: 18, alignItems: "center", justifyContent: "center", marginTop: 1 },
  privacyText: { flex: 1, color: colors.onSurface2, fontSize: 12, lineHeight: 17 },
  privacyLink: { color: colors.brand, fontWeight: "700", textDecorationLine: "underline" },
  privacyRoot: { flex: 1, backgroundColor: colors.surface },
  privacyHeader: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
  privacyBack: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  privacyTitle: { flex: 1, color: colors.onSurface, fontSize: 16, fontWeight: "900", textAlign: "center" },
  privacyHeaderSpacer: { width: 40 },
  privacyContent: { padding: spacing.lg, paddingBottom: 48 },
  privacyBody: { color: colors.onSurface2, fontSize: 13, lineHeight: 21 },
});