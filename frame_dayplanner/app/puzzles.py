"""Sum and word (klankzuiver) generators.

Both are deterministic for a given seed so the puzzle stays the same for the
whole day (or hour / session) even though the frame is redrawn every minute.
"""
from __future__ import annotations

import random
import re

# --------------------------------------------------------------------------
# Klankzuivere woorden.  Every grapheme maps to exactly one sound, no
# devoicing at the end (hond, web, rug), no schwa, no -nk.  The pattern
# (m = medeklinker, k = klinker) is computed automatically.
# --------------------------------------------------------------------------
WORDS_NL = """
ik in is op af om al as en uit ui aap oor uil ijs eet aan ook eis
zee koe zie doe nee mee fee bij hij wij zij jij mij bui wie die toe
vis kat pen bus pop mes tak kip pet sok zus hut dak bal bel jas kam lip mok
net pan pil pit rat rok tas ton vos wol zak kus zon mus bak bek bos dik dun
lam lek nat mol pot sap tol tik wit zit mat kok hok hek gat gek kan kom lus
mis nek pak pas rem rek ren vet vel vul wil zet zes tem bom
boom maan roos been vuur zeep muis huis deur boek voet raam kaas neus beer
buik duim haan haas hoek jaar koek kaal lijn maal mees mier muur neef poes
paal peer pijl poot reus riem rook soep tuin vaas veer vijf wiel zeil zoon
kool kous boos buis duif geit ruit wijn tien teen tuit vier vuil zaal zoet
huil kijk lach toch dier pijp rups
slak klok trap kruk bloem fles glas kraan plas slot snoep spin stok stip
trein vlek zwem krul knop prik smal slee stoel stoep steen stuur tram kleur
blik drop gras grap klap klim knie krat plan slim smul snor spek spel stal
ster stof stom trek vlok vrij zwaan spook spoor glim klep kroon
kast tent kist lamp melk wolk vest post kurk hert mast film bult gans kers
kort wesp wolf zalf half helm park pons rots zalm mist nest kust vork kalf
dorp golf
plant krant kwast trots
""".split()


VOWELS = ["aa", "ee", "oo", "uu", "oe", "ie", "eu", "ui", "ei", "ij", "ou", "au",
          "a", "e", "i", "o", "u"]
CONSONANT_DIGRAPHS = ["ch", "ng"]
SHORT_VOWELS = {"a", "e", "i", "o", "u"}


def sounds(word: str) -> list[str]:
    """Split a Dutch word into graphemes ('boom' -> ['b', 'oo', 'm'])."""
    out, i, w = [], 0, word.lower()
    while i < len(w):
        for g in VOWELS + CONSONANT_DIGRAPHS:
            if w.startswith(g, i):
                out.append(g)
                i += len(g)
                break
        else:
            out.append(w[i])
            i += 1
    return out


def pattern(word: str) -> str:
    return "".join("k" if s[0] in "aeiou" else "m" for s in sounds(word))


def pick_word(cfg, seed: str) -> str:
    custom = [w.strip() for w in (cfg.word_custom_list or []) if w and w.strip()]
    candidates = custom
    if not candidates:
        allowed = {p.strip().lower() for p in str(cfg.word_patterns).split(",") if p.strip()}
        max_len = int(cfg.word_max_letters or 99)
        candidates = [
            w for w in WORDS_NL
            if (not allowed or pattern(w) in allowed)
            and len(w) <= max_len
            and (cfg.word_digraphs or all(len(s) == 1 or s in CONSONANT_DIGRAPHS for s in sounds(w)))
        ]
    if not candidates:
        return "?"
    word = random.Random(f"word-{seed}").choice(sorted(set(candidates)))
    return word.upper() if cfg.word_case == "upper" else word


# --------------------------------------------------------------------------
# Sums
# --------------------------------------------------------------------------
OPS = {"+", "-", "x", ":"}


def make_sum(cfg, seed: str) -> str:
    """Return e.g. '3 + 4 = ?' respecting the configured max / operators."""
    rng = random.Random(f"sum-{seed}")
    ops = [o for o in re.split(r"[,\s]+", str(cfg.sum_operations).replace("*", "x").replace("/", ":")) if o in OPS] or ["+"]
    max_val = max(2, int(cfg.sum_max))
    terms = max(2, min(4, int(cfg.sum_terms)))

    # never use 0 as a number (9 + 0 is too easy); factors 2+ when the table allows it
    nums, ops_used, result = [1, 1], ["+"], 2  # fallback for impossible settings
    for _ in range(500):
        op = rng.choice(ops)
        if op in ("x", ":"):
            # multiplication tables: factors up to sum_max (max 10), product unlimited
            f = min(max_val, 10)
            low = 2 if f >= 3 else 1
            a, b = rng.randint(low, f), rng.randint(low, f)
            nums, ops_used, result = ([a, b], [op], a * b) if op == "x" else ([a * b, b], [op], a)
            break
        plus_minus = [x for x in ops if x in "+-"] or ["+"]
        first_op = rng.choice(plus_minus)
        if first_op == "+" and max_val >= 2:
            # every pair a + b <= max is equally likely (not mostly '+ 1')
            while True:
                a, b = rng.randint(1, max_val - 1), rng.randint(1, max_val - 1)
                if a + b <= max_val:
                    break
            total, cand = a + b, [a]
        else:
            total, cand = None, [rng.randint(1, max_val)]
        cand_ops = []
        value = cand[0]
        for i in range(terms - 1):
            o = first_op if i == 0 else rng.choice(plus_minus)
            if o == "+" and i == 0 and total:
                n = total - value
                value = total
            elif o == "+" and value < max_val:
                n = rng.randint(1, max_val - value)
                value += n
            elif o == "-" and value >= 1:
                n = rng.randint(1, value)
                value -= n
            else:
                break
            cand.append(n)
            cand_ops.append(o)
        if len(cand) == terms:
            nums, ops_used, result = cand, cand_ops, value
            break

    parts = [str(nums[0])]
    for o, n in zip(ops_used, nums[1:]):
        parts += [o, str(n)]
    if cfg.sum_missing == "random" and rng.random() < 0.5:
        idx = rng.randrange(len(nums))
        parts[idx * 2] = "?"
        return " ".join(parts) + f" = {result}"
    return " ".join(parts) + " = ?"


def seed_for(now, refresh: str, session_id: str = "") -> str:
    if refresh == "hourly":
        return now.strftime("%Y%m%d%H")
    if refresh == "session":
        return session_id or now.strftime("%Y%m%d")
    return now.strftime("%Y%m%d")
