"""Build frame_dayplanner/app/fonts/emoji_index.json (run once, result is committed).

    python tools/build_emoji_index.py

Sources (pinned to match the bundled Noto Color Emoji v2.047 = Emoji 15.1):
  * Unicode emoji-test.txt 15.1        -> emoji, English name, group
  * Unicode CLDR 46 annotations (en/nl) -> Dutch names and search keywords
Skin-tone variants and components are left out.
"""
import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frame_dayplanner" / "app" / "fonts" / "emoji_index.json"
EMOJI_TEST = "https://unicode.org/Public/emoji/15.1/emoji-test.txt"
CLDR = "https://raw.githubusercontent.com/unicode-org/cldr-json/46.0.0/cldr-json/{kind}/{sub}/{lang}/annotations.json"
SKIN = re.compile("[\U0001F3FB-\U0001F3FF]")


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - fixed https URLs
        return r.read().decode("utf-8")


def annotations(lang: str) -> dict:
    out = {}
    for kind, sub in (("cldr-annotations-full", "annotations"), ("cldr-annotations-derived-full", "annotationsDerived")):
        data = json.loads(fetch(CLDR.format(kind=kind, sub=sub, lang=lang)))
        for ch, v in data[sub]["annotations"].items():
            out.setdefault(ch.replace("️", ""), v)
    return out


def main() -> None:
    en, nl = annotations("en"), annotations("nl")
    rows, group = [], ""
    for line in fetch(EMOJI_TEST).splitlines():
        if line.startswith("# group:"):
            group = line.split(":", 1)[1].strip()
            continue
        m = re.match(r"^[0-9A-F ]+;\s*fully-qualified\s*#\s*(\S+)\s+E[\d.]+\s+(.+)$", line)
        if not m or group == "Component":
            continue
        emoji, name = m.group(1), m.group(2).strip()
        if SKIN.search(emoji):
            continue
        key = emoji.replace("️", "")
        a_en, a_nl = en.get(key, {}), nl.get(key, {})
        name_nl = (a_nl.get("tts") or [""])[0]
        keywords = sorted({k.lower() for k in a_en.get("default", []) + a_nl.get("default", [])} - {name.lower(), name_nl.lower()})
        rows.append([emoji, name, name_nl, " ".join(keywords), group])
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {len(rows)} emoji to {OUT} ({OUT.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    main()
