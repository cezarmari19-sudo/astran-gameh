"""Filtrul de chat pentru jucatorii SUB 18 ani (stdlib only - testabil fara FastAPI/Mongo).

============================================================================
CONFIGURARE (fara modificarea codului)
============================================================================
Regulile sunt DATE, nu cod. Sursa lor, in ordinea prioritatii:
  1. baza de date: platform_config {"key": "chat_filter_rules", "rules": [...]} - editabila
     prin API-ul de admin (chat_admin_routes.py), efect in maxim RULES_TTL_SECONDS secunde,
     pe toate procesele backend;
  2. fisierul backend/config/chat_filter_rules.json (sau calea din ASTRAN_CHAT_FILTER_RULES) -
     reincarcat automat cand i se schimba data modificarii.
Daca o versiune noua e invalida, se pastreaza ULTIMA versiune buna (si se logheaza eroarea).

Formatul unei reguli (toate campurile in afara de "term" sunt optionale):
    {
      "id": "r_badword",            # unic; implicit derivat din term
      "term": "badword",            # cuvant sau expresie (cu spatii)
      "type": "word",               # "word" | "expression" (informativ; spatiile fac expresia)
      "action": "mask_word",        # "mask_word": doar cuvantul -> #####
                                    # "mask_sentence": tot mesajul -> ###############
      "match": "whole_word",        # "whole_word" (implicit) | "contains"
      "obfuscation": true,          # tolereaza leet (b4dw0rd), litere repetate (baaadword),
                                    # separatori (b.a.d.w.o.r.d / b a d w o r d, doar termeni >= 4 litere)
      "enabled": true
    }

Majuscule/minuscule, diacritice (ă->a), caractere zero-width si litere "asemanatoare" din
alte alfabete (chirilic/grecesc) sunt neutralizate INAINTE de potrivire, iar potrivirea se
mapeaza inapoi pe textul ORIGINAL - de aceea se maschează exact segmentul gasit.

Daca un mesaj potriveste mai multe reguli: orice regula "mask_sentence" ascunde tot mesajul;
altfel se maschează fiecare segment potrivit de reguli "mask_word" (suprapunerile se unesc),
restul propozitiei ramane vizibil.

LIMITARI REALE (filtrarea pe liste nu poate fi perfecta): nu intelege context/sens, nu prinde
orice varianta inventata (ex. litere inlocuite cu emoji, scris foarte fragmentat cu >2
separatori), si poate masca un cuvant legitim doar daca regula e "contains". Lista trebuie
intretinuta de moderatori.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

log = logging.getLogger("astran.chat_filter")

ACTION_MASK_WORD = "mask_word"
ACTION_MASK_SENTENCE = "mask_sentence"
ACTIONS = (ACTION_MASK_WORD, ACTION_MASK_SENTENCE)
MATCH_WHOLE_WORD = "whole_word"
MATCH_CONTAINS = "contains"
MATCH_MODES = (MATCH_WHOLE_WORD, MATCH_CONTAINS)
KINDS = ("word", "expression")

MAX_RULES = 2000
MAX_TERM_LEN = 80
MASK_CHAR = "#"
RULES_TTL_SECONDS = 15.0
CONFIG_KEY = "chat_filter_rules"
DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "chat_filter_rules.json"


class RuleError(ValueError):
    """Regula/lista de reguli e invalida (mesajul e destinat adminului)."""


# --------------------------------------------------------------------------------------
# Normalizare
# --------------------------------------------------------------------------------------
# Caractere pe care le ignoram complet la potrivire (invizibile - folosite ca sa "sparga" un cuvant).
_IGNORABLE = set(
    list(range(0x200B, 0x2010)) + [0x00AD, 0x034F, 0x061C, 0x180E, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF]
    + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A))
)

# Litere din alte alfabete care arata ca litere latine (homoglife frecvente).
_CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ѕ": "s", "ј": "j",
    "ԁ": "d", "һ": "h", "ӏ": "l", "к": "k", "м": "m", "н": "h", "т": "t", "в": "b",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ν": "v",
    "ß": "ss", "ø": "o", "đ": "d", "ł": "l", "ı": "i", "æ": "ae", "œ": "oe",
}

# Echivalente "leet" admise intr-un termen cu obfuscation=true (pozitie cu pozitie).
_LEET = {
    "a": "4@", "b": "8", "e": "3", "g": "9", "i": "1", "l": "1", "o": "0", "s": "5$", "t": "7", "z": "2",
}
# Separatorii admisi intre literele unui termen NU contin caracterele leet (@, $) si nici litere/cifre:
# astfel un caracter poate fi consumat de exact o alternativa din pattern => fara backtracking
# exponential, indiferent de textul trimis de un atacator.
_SEP = r"(?:[^\w@$]|_){0,2}"
_ALNUM_BEFORE = r"(?<![^\W_])"
_ALNUM_AFTER = r"(?![^\W_])"


@dataclass(frozen=True)
class Folded:
    text: str
    starts: tuple  # starts[i] = indexul (in textul original) unde incepe caracterul i din `text`
    ends: tuple    # ends[i]   = indexul (exclusiv) unde se termina


def fold(original: str) -> Folded:
    """Text normalizat (litere mici, fara diacritice/zero-width, homoglife -> latin) +
    maparea fiecarui caracter normalizat inapoi pe pozitia lui in textul original. Sirurile
    repetate de aceeasi litera se comprima (baaad -> bad): `ends` acopera tot sirul original."""
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for i, ch in enumerate(original):
        if ord(ch) in _IGNORABLE:
            # un caracter invizibil in interiorul unui segment potrivit ramane in interiorul lui;
            # daca il urmeaza o litera comprimata, extindem sfarsitul ultimului caracter
            if ends:
                ends[-1] = max(ends[-1], i + 1)
            continue
        for d in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(d):
                if ends:
                    ends[-1] = max(ends[-1], i + 1)
                continue
            d = _CONFUSABLES.get(d.casefold(), d.casefold())
            for c in d:
                if out and out[-1] == c and c.isalpha():
                    ends[-1] = i + 1  # aceeasi litera repetata: o comprimam, dar o acoperim in span
                    continue
                out.append(c)
                starts.append(i)
                ends.append(i + 1)
    return Folded("".join(out), tuple(starts), tuple(ends))


def _fold_term(term: str) -> str:
    return fold(term).text


# --------------------------------------------------------------------------------------
# Reguli
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    id: str
    term: str
    kind: str
    action: str
    match: str
    obfuscation: bool
    enabled: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id, "term": self.term, "type": self.kind, "action": self.action,
            "match": self.match, "obfuscation": self.obfuscation, "enabled": self.enabled,
        }


def _derive_id(term: str) -> str:
    ascii_term = "".join(c for c in unicodedata.normalize("NFKD", term.casefold()) if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_term).strip("_")
    return f"r_{slug or 'rule'}"[:64]


def rule_from_dict(raw: Any, index: int = 0) -> Rule:
    where = f"regula #{index + 1}"
    if not isinstance(raw, dict):
        raise RuleError(f"{where}: trebuie sa fie un obiect")
    term = raw.get("term")
    if not isinstance(term, str) or not term.strip():
        raise RuleError(f"{where}: 'term' lipseste sau e gol")
    term = " ".join(term.split())
    if len(term) > MAX_TERM_LEN:
        raise RuleError(f"{where}: 'term' depaseste {MAX_TERM_LEN} caractere")
    if not any(c.isalnum() for c in term):
        raise RuleError(f"{where}: 'term' trebuie sa contina litere sau cifre")
    action = raw.get("action", ACTION_MASK_WORD)
    if action not in ACTIONS:
        raise RuleError(f"{where}: 'action' trebuie sa fie una din {ACTIONS}")
    match = raw.get("match", MATCH_WHOLE_WORD)
    if match not in MATCH_MODES:
        raise RuleError(f"{where}: 'match' trebuie sa fie una din {MATCH_MODES}")
    kind = raw.get("type", "expression" if " " in term else "word")
    if kind not in KINDS:
        raise RuleError(f"{where}: 'type' trebuie sa fie una din {KINDS}")
    for key in ("obfuscation", "enabled"):
        if key in raw and not isinstance(raw[key], bool):
            raise RuleError(f"{where}: '{key}' trebuie sa fie true/false")
    rid = raw.get("id") or _derive_id(term)
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", rid):
        raise RuleError(f"{where}: 'id' invalid (litere/cifre/_ . : - , max 64)")
    return Rule(
        id=rid, term=term, kind=kind, action=action, match=match,
        obfuscation=raw.get("obfuscation", True), enabled=raw.get("enabled", True),
    )


def parse_rules(data: Any) -> list[Rule]:
    """Accepta {"rules": [...]} sau direct o lista. Ridica RuleError la prima problema -
    o lista partiala/gresita NU se aplica (altfel un typo ar dezactiva silentios filtrul)."""
    items = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuleError("trebuie 'rules': [ ... ]")
    if len(items) > MAX_RULES:
        raise RuleError(f"prea multe reguli (max {MAX_RULES})")
    rules: list[Rule] = []
    seen: set[str] = set()
    for i, raw in enumerate(items):
        r = rule_from_dict(raw, i)
        if r.id in seen:
            raise RuleError(f"id duplicat: {r.id}")
        seen.add(r.id)
        rules.append(r)
    return rules


def _term_pattern(rule: Rule) -> Optional[str]:
    folded = _fold_term(rule.term)
    tokens = [t for t in re.split(r"\s+", folded) if t]
    if not tokens:
        return None
    parts: list[str] = []
    for tok in tokens:
        letters = [c for c in tok]
        use_sep = rule.obfuscation and sum(c.isalnum() for c in letters) >= 4
        pieces: list[str] = []
        for c in letters:
            if rule.obfuscation and c in _LEET:
                pieces.append("[" + re.escape(c) + re.escape(_LEET[c]) + "]")
            else:
                pieces.append(re.escape(c))
        parts.append((_SEP if use_sep else "").join(pieces))
    between = r"(?:[^\w@$]|_)+" if rule.obfuscation else r"\s+"
    body = between.join(parts)
    if rule.match == MATCH_WHOLE_WORD:
        body = _ALNUM_BEFORE + body + _ALNUM_AFTER
    return body


@dataclass(frozen=True)
class CompiledRule:
    rule: Rule
    regex: "re.Pattern[str]"


def compile_rules(rules: Iterable[Rule]) -> list[CompiledRule]:
    out: list[CompiledRule] = []
    for r in rules:
        if not r.enabled:
            continue
        pat = _term_pattern(r)
        if pat is None:
            continue
        out.append(CompiledRule(r, re.compile(pat)))
    return out


# --------------------------------------------------------------------------------------
# Filtrare
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FilterResult:
    text: str
    changed: bool
    sentence_blocked: bool
    matched_rule_ids: tuple


def filter_text(text: str, compiled: list[CompiledRule]) -> FilterResult:
    """Aplica regulile. Nu modifica textul daca nu se potriveste nimic."""
    if not text or not compiled:
        return FilterResult(text, False, False, ())
    f = fold(text)
    if not f.text:
        return FilterResult(text, False, False, ())
    spans: list[tuple[int, int]] = []
    matched: list[str] = []
    sentence = False
    for c in compiled:
        hit = False
        for m in c.regex.finditer(f.text):
            if m.end() <= m.start():
                continue
            hit = True
            if c.rule.action == ACTION_MASK_SENTENCE:
                sentence = True
                break
            spans.append((f.starts[m.start()], f.ends[m.end() - 1]))
        if hit:
            matched.append(c.rule.id)
        if sentence:
            break
    if sentence:
        return FilterResult(MASK_CHAR * len(text), True, True, tuple(matched))
    if not spans:
        return FilterResult(text, False, False, ())
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    chars = list(text)
    for s, e in merged:
        for i in range(s, e):
            chars[i] = MASK_CHAR
    return FilterResult("".join(chars), True, False, tuple(matched))


# --------------------------------------------------------------------------------------
# Sursa regulilor (fisier + baza de date), cu cache si "ultima versiune buna"
# --------------------------------------------------------------------------------------
class RuleStore:
    """db = obiectul Motor (db.platform_config) sau None (doar fisier). Toate apelurile
    catre db sunt prinse: o cadere a bazei de date NU opreste chatul (se pastreaza regulile
    deja incarcate), si NU face chatul sa treaca pe "fara filtru" daca avem deja reguli."""

    def __init__(
        self,
        db: Any = None,
        path: Optional[Path] = None,
        ttl: float = RULES_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self._path = Path(os.environ.get("ASTRAN_CHAT_FILTER_RULES") or path or DEFAULT_RULES_PATH)
        self._ttl = ttl
        self._clock = clock
        self._compiled: list[CompiledRule] = []
        self._rules: list[Rule] = []
        self._source = "none"
        self._loaded_at: Optional[float] = None
        self._file_mtime: Optional[float] = None

    @property
    def source(self) -> str:
        return self._source

    def invalidate(self) -> None:
        self._loaded_at = None

    def _load_file(self) -> Optional[list[Rule]]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            rules = parse_rules(data)
        except (OSError, ValueError) as exc:  # json.JSONDecodeError e ValueError; RuleError idem
            log.error("chat_filter: %s invalid (%s) - pastrez regulile anterioare", self._path, exc)
            self._file_mtime = mtime  # nu reincercam pana nu se schimba fisierul
            return None
        self._file_mtime = mtime
        return rules

    async def _load_db(self) -> Optional[list[Rule]]:
        if self._db is None:
            return None
        try:
            doc = await self._db.platform_config.find_one({"key": CONFIG_KEY}, {"_id": 0})
        except Exception:  # noqa: BLE001
            log.warning("chat_filter: citirea regulilor din DB a esuat", exc_info=True)
            return None
        if not doc:
            return None
        try:
            return parse_rules(doc.get("rules", []))
        except RuleError as exc:
            log.error("chat_filter: regulile din DB sunt invalide (%s) - pastrez cele anterioare", exc)
            return None

    def _apply(self, rules: list[Rule], source: str) -> None:
        self._rules = rules
        self._compiled = compile_rules(rules)
        self._source = source

    async def get_compiled(self) -> list[CompiledRule]:
        now = self._clock()
        if self._loaded_at is not None and now - self._loaded_at < self._ttl:
            return self._compiled
        db_rules = await self._load_db()
        if db_rules is not None:
            self._apply(db_rules, "db")
        else:
            had_db = self._source == "db" and self._db is not None
            file_rules = self._load_file()
            if file_rules is not None and not had_db:
                self._apply(file_rules, "file")
            elif not self._rules and file_rules is not None:
                self._apply(file_rules, "file")
        self._loaded_at = now
        return self._compiled

    async def get_rules(self) -> list[Rule]:
        await self.get_compiled()
        return list(self._rules)

    async def save_rules(self, rules: list[Rule]) -> None:
        """Salveaza lista (deja validata) in DB si o aplica imediat pe acest proces; celelalte
        procese o iau la urmatoarea expirare a cache-ului (RULES_TTL_SECONDS)."""
        if self._db is None:
            raise RuntimeError("baza de date indisponibila")
        await self._db.platform_config.update_one(
            {"key": CONFIG_KEY},
            {"$set": {"key": CONFIG_KEY, "rules": [r.to_dict() for r in rules]}},
            upsert=True,
        )
        self._apply(rules, "db")
        self._loaded_at = self._clock()


_SHARED: Optional[RuleStore] = None


def shared_store(db: Any = None) -> RuleStore:
    """Un singur RuleStore pe proces, folosit si de WebSocket (realtime.py) si de API-ul de admin."""
    global _SHARED
    if _SHARED is None:
        _SHARED = RuleStore(db)
    return _SHARED