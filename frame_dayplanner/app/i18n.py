"""Translations for text shown on the planner."""

STRINGS = {
    "nl": {
        "days": ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"],
        "days_short": ["ma", "di", "wo", "do", "vr", "za", "zo"],
        "months": ["januari", "februari", "maart", "april", "mei", "juni", "juli",
                   "augustus", "september", "oktober", "november", "december"],
        "periods": ["ochtend", "middag", "avond"],
        "in_minutes": "nog {n} min",
        "leave_label": "{title}: vertrekken",
    },
    "en": {
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        "days_short": ["mo", "tu", "we", "th", "fr", "sa", "su"],
        "months": ["january", "february", "march", "april", "may", "june", "july",
                   "august", "september", "october", "november", "december"],
        "periods": ["morning", "afternoon", "evening"],
        "in_minutes": "in {n} min",
        "leave_label": "{title}: time to leave",
    },
}


def t(lang: str) -> dict:
    return STRINGS.get(lang, STRINGS["nl"])
