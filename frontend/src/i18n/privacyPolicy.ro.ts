// GENERAT din backend/legal/privacy_policy/1.1.ro.txt de backend/legal/sync_frontend_policy.py - NU edita manual.
export const PRIVACY_POLICY_RO = `ASTRAN GAME — POLITICA DE CONFIDENȚIALITATE

Versiune: 1.1
Stare: CIORNĂ — NECESITĂ COMPLETARE (vezi marcajele [DE COMPLETAT] de mai jos; acest document nu trebuie prezentat ca final cât timp există un marcaj)
Data intrării în vigoare: [DE COMPLETAT: data intrării în vigoare]
Operator: [DE COMPLETAT: denumirea legală, nr. de înregistrare și sediul operatorului]
E-mail pentru confidențialitate: [DE COMPLETAT: adresa de e-mail pentru confidențialitate]


1. CINE SUNTEM ȘI CE ACOPERĂ ACEASTĂ POLITICĂ

Astran Game („Astran”, „noi”) este o platformă pentru crearea, publicarea și jucarea de jocuri și alt conținut generat de utilizatori. Această politică explică ce date personale prelucrează aplicația Astran și serverele ei, de ce, pe ce temei juridic, cine le primește, cât timp se păstrează și ce drepturi ai (Regulamentul (UE) 2016/679, „GDPR”, art. 5 și 12-14).

Astran nu folosește publicitate, profilare comportamentală, SDK-uri de analiză sau de raportare a erorilor, nu antrenează modele de inteligență artificială pe datele utilizatorilor și nu vinde și nu licențiază date personale. Dacă acest lucru se schimbă, politica va fi actualizată înainte ca schimbarea să intre în vigoare.


2. DATELE PRELUCRATE, SCOPURI ȘI TEMEI JURIDIC

2.1. Cont și autentificare
Date: adresa de e-mail, numele de utilizator, numele afișat, parola (stocată doar ca hash cu sare), limba preferată, ID-ul contului, datele de creare și actualizare, starea verificării e-mailului, referința imaginii de profil, soldul de Astrans, tokenuri de sesiune (valabile 7 zile), coduri de resetare a parolei și de verificare a e-mailului (de scurtă durată) și, dacă înregistrezi autentificarea cu passkey/biometrie (WebAuthn), cheia publică și numele credențialului (amprenta sau fața rămân pe dispozitivul tău și nu ajung la noi). Dacă te autentifici cu Google, primim e-mailul, numele și poza de profil printr-un broker de autentificare (vezi secțiunea 4).
Scop: crearea și securizarea contului, autentificare, furnizarea serviciului.
Temei: executarea contractului (art. 6 alin. (1) lit. b); securitatea serviciului (art. 6 alin. (1) lit. f).

2.2. Vârstă și acord parental
Date: data nașterii (stocată criptat), banda/categoria de vârstă, dacă s-a finalizat verificarea vârstei; pentru utilizatorii sub 16 ani, e-mailul părintelui sau tutorelui (stocat criptat), decizia parentală, data ei și înregistrări tehnice ale pașilor de verificare; linkurile de unică folosință trimise părintelui se stochează doar ca hash-uri cu cheie și expiră. Evenimentele legate de vârstă și acord parental se păstrează într-un jurnal de audit care evidențiază modificările, cu identificatori pseudonimizați (fără data nașterii, e-mail sau IP).
Scop: aplicarea protecțiilor adecvate vârstei, obținerea și dovedirea acordului parental unde este cerut.
Temei: obligație legală și protecția minorilor (art. 6 alin. (1) lit. c/f); consimțământul titularului răspunderii părintești (art. 8 GDPR).
Furnizori opționali: dacă este configurat un furnizor extern pentru verificarea vârstei 18+ sau a calității de părinte, ești redirecționat către el, iar noi primim doar un rezultat semnat. [DE COMPLETAT: numele furnizorului/furnizorilor folosiți efectiv sau confirmarea că nu se folosește niciunul]

2.3. Jocuri, avatar și creații
Date: jocuri, scripturi, resurse, modele 3D, configurația avatarului și a hainelor, obiecte publicate sau cumpărate, jocuri jucate recent, roluri de colaborator/tester și prezența într-o sesiune de joc (expiră automat după aproximativ 60 de secunde de inactivitate). Mișcarea și evenimentele din joc sunt transmise în timp real celorlalți jucători din aceeași sesiune.
Scop: funcționarea jocurilor multiplayer, a Studio și a magazinului.
Temei: contract (art. 6 alin. (1) lit. b).

2.4. Chat și mesaje
- Chatul din joc este transmis jucătorilor din aceeași sesiune și păstrat doar într-o memorie tampon scurtă; se stochează doar dacă este raportat.
- Mesajele din chatul de grup și mesajele private între prieteni sunt stocate pe serverele noastre. Pentru expeditorii sub 18 ani, textul nefiltrat al mesajelor private nu se stochează.
- Pentru utilizatorii sub 18 ani, mesajele sunt filtrate automat după reguli fixe de cuvinte/expresii (fără IA); minorii primesc textul filtrat.
- Mesajele pot fi examinate de moderatori când sunt raportate.
Scop: mesagerie, protecția minorilor, moderarea abuzurilor.
Temei: contract (art. 6 alin. (1) lit. b); interes legitim de siguranță (art. 6 alin. (1) lit. f); obligații legale (Regulamentul privind serviciile digitale).

2.5. Prieteni, grupuri și funcții sociale
Date: lista de prieteni și cererile, utilizatori blocați, grupuri, calitatea de membru, roluri, stare de mute.
Scop și temei: furnizarea funcțiilor sociale (art. 6 alin. (1) lit. b).

2.6. Rapoarte și moderare
Date: rapoartele trimise de tine sau care te privesc, contextul mesajelor și dovezile atașate unui raport, deciziile moderatorilor și expunerile de motive, contestațiile și notificările, înregistrări de audit ale acțiunilor moderatorilor și mesajele trimise prin formularul de contact (nume, e-mail, subiect, mesaj). Deciziile de moderare sunt luate de oameni; instrumentele automate pot doar ajuta la detectarea conținutului.
Scop: tratarea rapoartelor, aplicarea regulilor, motivarea și contestarea deciziilor, răspunsul la solicitări.
Temei: obligație legală (art. 6 alin. (1) lit. c); interes legitim pentru o platformă sigură (art. 6 alin. (1) lit. f).

2.7. Achiziții și monedă virtuală
Date: tranzacții cu Astrans (registru), achiziții de obiecte din magazin/haine, achiziții de pachete de Astrans (referință furnizor, sumă, stare), cereri și decizii de rambursare, evidențe privind drepturile consumatorului. Nu primim și nu stocăm numere complete de card: plățile sunt procesate de Google Play, Apple sau Stripe, iar noi primim identificatori și stare și trimitem tokenul/chitanța achiziției furnizorului pentru verificare.
Scop: procesarea plăților, prevenirea fraudei, contabilitate, rambursări, drepturile consumatorului.
Temei: contract (art. 6 alin. (1) lit. b); obligații legale, de ex. contabile (art. 6 alin. (1) lit. c); prevenirea fraudei (art. 6 alin. (1) lit. f).

2.8. Securitate, jurnale și prevenirea abuzurilor
Date: adresa IP în înregistrările de evenimente de securitate (de ex. resetarea parolei, ștergerea sau recuperarea unui proiect) și pentru limitarea ratei de cereri; contoare de încercări eșuate; înregistrări de tokenuri revocate; jurnale de server cu date tehnice ale cererilor (tokenurile de acces sunt mascate).
Scop: protejarea conturilor, detectarea abuzurilor și fraudei.
Temei: interes legitim (art. 6 alin. (1) lit. f).

2.9. Dovada consimțământului și consimțământul opțional pentru marketing
Stocăm dovada versiunii Termenilor și a acestei Politici pe care le-ai acceptat sau citit (versiune, dată, amprenta textului; fără IP sau date despre dispozitiv). Utilizatorii de la 16 ani pot da consimțământ opțional pentru e-mailuri de marketing; nu este necesar pentru a folosi Astran și poate fi retras oricând în Setări. Temei: obligație legală / interes legitim de a dovedi conformitatea (art. 6 alin. (1) lit. c/f); consimțământ pentru marketing (art. 6 alin. (1) lit. a).

2.10. Date pe care nu le colectăm
Astran nu cere acces la microfon, cameră sau locație, nu folosește identificatori publicitari ai dispozitivului, notificări push, SDK-uri de analiză sau de publicitate și nu oferă chat vocal sau video.


3. DECIZII AUTOMATE

Astran nu ia decizii bazate exclusiv pe prelucrare automată care produc efecte juridice sau similar de semnificative asupra ta (art. 22 GDPR). Filtrul automat de chat pentru utilizatorii sub 18 ani maschează cuvinte sau mesaje după reguli fixe; poți contesta un rezultat prin formularul de contact.


4. DESTINATARI

Nu vindem și nu licențiem date personale. Datele sunt divulgate doar către:
- furnizori de infrastructură care găzduiesc serverele, baza de date și (dacă este activat) memoria cache pentru mesageria în timp real [DE COMPLETAT: nume și locații];
- un broker de autentificare și Google, pentru „Sign in with Google” (serviciul de autentificare Emergent și Google);
- Google Play, Apple și Stripe, pentru verificarea plăților, rambursări și anulări;
- un furnizor de livrare e-mail pentru e-mailurile de verificare, resetare a parolei și cele către părinți [DE COMPLETAT: furnizor];
- furnizori de verificare a vârstei și a calității de părinte, doar dacă sunt configurați (vezi 2.2);
- părinții sau tutorii utilizatorilor sub 16 ani, pentru datele necesare deciziei lor;
- autorități competente, instanțe și alte părți, când legea o cere;
- alți utilizatori, pentru conținutul pe care alegi să-l faci vizibil (nume de utilizator, nume afișat, avatar, jocuri, mesaje de chat).
Furnizorii acționează ca persoane împuternicite, conform instrucțiunilor noastre, cu excepția cazurilor în care acționează ca operatori independenți (de ex. Google, Apple, Stripe pentru propriile servicii).


5. TRANSFERURI INTERNAȚIONALE

Unii destinatari (de ex. Google, Apple, Stripe, furnizori de găzduire) pot prelucra date în afara SEE. [DE COMPLETAT: pentru fiecare transfer, garanția folosită — decizie de adecvare, clauze contractuale standard sau alta — și cum se poate obține o copie.]


6. PĂSTRAREA DATELOR

Datele se păstrează cât timp există contul, cu excepția cazurilor în care este indicată o perioadă mai scurtă. Perioade tehnice actuale:
- sesiuni: 7 zile; coduri de resetare a parolei și de verificare e-mail: până la expirare; prezența într-un joc: aproximativ 60 de secunde;
- arhivele de export de date: șterse automat după 24 de ore;
- elementele din coșul de proiecte: șterse definitiv după 30 de zile;
- sesiunile de verificare a vârstei: șterse automat în maximum 24 de ore; linkurile parentale de unică folosință expiră (7 zile pentru decizie, 30 de zile pentru revendicare);
- înregistrările de securitate ale conturilor active nu sunt în prezent șterse după un termen fix.
După ștergerea contului, datele care trebuie păstrate sunt pseudonimizate și eliminate la expirarea termenului: evidențele achizițiilor cu bani reali 5 ani (contabilitate/anti-fraudă), dovada consimțământului 3 ani, rapoartele de moderare 365 de zile, înregistrările de securitate 365 de zile (fără IP). Aceste perioade sunt setările actuale ale platformei. [DE COMPLETAT: operatorul confirmă aceste perioade și justificarea lor legală]
Copii de rezervă: [DE COMPLETAT: perioada de păstrare a copiilor de rezervă]


7. ȘTERGEREA CONTULUI

Îți poți șterge contul din Setări (cu re-autentificare și confirmare explicită). Sesiunile sunt revocate; prietenii, blocările, setările, avatarul, jocurile deținute, modelele, colaborările și jocurile recente sunt șterse; proprietatea grupurilor este transferată sau grupul este eliminat; mesajele directe sunt șterse sau partea ta este pseudonimizată; obiectele din magazin cumpărate de alții sunt retrase din vânzare și anonimizate; evidențele din secțiunea 6 sunt pseudonimizate și păstrate pe perioadele indicate. Mesajele tale din grupuri raportate moderatorilor pot fi păstrate pseudonimizat până la sfârșitul perioadei de moderare.


8. COPII ȘI MINORI

- Utilizatorii introduc data nașterii la înregistrare; banda de vârstă nu poate fi ridicată ulterior fără un rezultat de verificare a vârstei.
- Sub 16 ani: contul rămâne restricționat până când un părinte sau tutore își dă acordul prin fluxul de verificare; părinții pot consulta, retrage acordul și cere acțiuni asupra datelor prin panoul parental.
- Sub 18 ani: chat filtrat, fără stocarea textului nefiltrat al mesajelor private, contact restricționat cu adulții și fără prelucrări opționale bazate pe consimțământ sub 16 ani.
- Accesul 18+ cere verificarea vârstei de către un furnizor extern; fără furnizor, funcțiile 18+ rămân blocate.


9. DREPTURILE TALE ȘI CUM LE EXERCIȚI

Ai dreptul de acces, rectificare, ștergere, restricționare, portabilitate și opoziție, precum și dreptul de a-ți retrage oricând consimțământul, fără a afecta prelucrările anterioare (art. 15-21 și 7 alin. (3) GDPR).
- Acces și portabilitate: Setări › Confidențialitate și securitate › Datele tale › Download Your Data (arhivă ZIP).
- Ștergere: Setări › Confidențialitate și securitate (ștergerea contului).
- Rectificare și preferințe: profil și Setări; gestionarea consimțămintelor în Setări.
- Orice altceva: scrie-ne la adresa de e-mail de confidențialitate de mai sus. Răspundem în cel mult o lună (prelungibilă cu două luni pentru cereri complexe).
Poți depune o plângere la Autoritatea Națională de Supraveghere a Prelucrării Datelor cu Caracter Personal (ANSPDCP, www.dataprotection.ro) sau la autoritatea din țara ta de reședință. Furnizarea datelor de la 2.1 și 2.2 este necesară pentru crearea unui cont; fără ele nu putem furniza serviciul.


10. COOKIE-URI ȘI STOCARE LOCALĂ

Aplicația mobilă nu folosește cookie-uri. Stochează tokenul de sesiune și setările pe dispozitivul tău (stocare securizată / stocare locală) pentru a te menține autentificat și a reține preferințele. Versiunea web folosește stocarea locală a browserului în același scop. Nu se folosesc cookie-uri sau tehnologii similare de urmărire ori publicitate.


11. MODIFICĂRI

Modificările materiale se publică ca versiune nouă; ți se poate cere să o citești și să o accepți din nou.


12. CONTACT

Operator: [DE COMPLETAT: denumirea legală și adresa]
E-mail: [DE COMPLETAT: e-mail de confidențialitate]
Responsabil cu protecția datelor (dacă a fost desemnat): [DE COMPLETAT: nume/contact sau „nu a fost desemnat”]

SFÂRȘITUL POLITICII DE CONFIDENȚIALITATE
`;
