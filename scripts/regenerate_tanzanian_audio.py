#!/usr/bin/env python3
"""Regenerate read-aloud clips with corrected Tanzanian Swahili speech.

Visible textbook strings remain authoritative. This module creates a separate
spoken representation and never rewrites texts.json or page HTML.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOICE = "sw-TZ-DaudiNeural"
# Retained as an import-compatible alias for older audit tooling. Every spoken
# segment now uses Daudi, including acronyms, URLs, and email addresses.
ENGLISH_VOICE = DEFAULT_VOICE
DEFAULT_RATE = "-5%"
LANGS = ("sw", "sw-TZ")

TOC_PAGE_NUMBERS = {
    "pg003_page_roman_v": 5,
    "pg003_page_roman_vi": 6,
    "pg003_page_1": 1,
    "pg003_page_9": 9,
    "pg003_page_13": 13,
    "pg003_page_19": 19,
    "pg003_page_33": 33,
    "pg003_page_60": 60,
    "pg003_page_77": 77,
    "pg004_page_91": 91,
    "pg004_page_101": 101,
    "pg004_page_117": 117,
    "pg004_page_130": 130,
}
TOC_ROMAN_PAGE_NUMBERS = {"pg003_page_roman_v": 5, "pg003_page_roman_vi": 6}

PAGE_THIRTEEN_COLUMN_SPEECH = {}

# Page 39 in this edition is the chapter opener for addition. Older pipeline
# overrides targeted a different page and would misread the current content.
PAGE_THIRTY_NINE_EQUATION_SPEECH = {}

PAGE_EIGHTY_THREE_PLACE_VALUE_SPEECH = {
    "pg083_n0009": "Kumi na tisa. Tisa ipo kwenye nafasi ya dashi. Moja ipo kwenye nafasi ya dashi.",
    "pg083_n0018": "Kumi. Sifuri ipo kwenye nafasi ya dashi. Moja ipo kwenye nafasi ya dashi.",
    "pg083_n0027": "Thelathini na nne. Nne ipo kwenye nafasi ya dashi. Tatu ipo kwenye nafasi ya dashi.",
    "pg083_n0036": "Ishirini na tatu. Tatu ipo kwenye nafasi ya dashi. Mbili ipo kwenye nafasi ya dashi.",
    "pg083_n0045": "Arobaini na tano. Tano ipo kwenye nafasi ya dashi. Nne ipo kwenye nafasi ya dashi.",
    "pg083_n0013": "Thelathini. Sifuri ipo kwenye nafasi ya dashi. Tatu ipo kwenye nafasi ya dashi.",
    "pg083_n0022": "Kumi na sita. Sita ipo kwenye nafasi ya dashi. Moja ipo kwenye nafasi ya dashi.",
    "pg083_n0031": "Tatu. Tatu ipo kwenye nafasi ya dashi.",
    "pg083_n0040": "Tisa. Tisa ipo kwenye nafasi ya dashi.",
    "pg083_n0049": "Sita. Sita ipo kwenye nafasi ya dashi.",
}

ONES = (
    "sifuri", "moja", "mbili", "tatu", "nne", "tano", "sita", "saba",
    "nane", "tisa",
)
TENS = (
    "", "kumi", "ishirini", "thelathini", "arobaini", "hamsini",
    "sitini", "sabini", "themanini", "tisini",
)
ORDINALS = {1: "kwanza", 2: "pili", 3: "tatu", 4: "nne", 5: "tano", 6: "sita"}
ROMAN_NUMERALS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
LIST_LETTERS = {
    "a": "a", "b": "be", "c": "che", "d": "de", "e": "e", "f": "efu",
    "g": "ge", "h": "ha", "i": "i", "j": "je", "k": "ka", "l": "ele",
    "m": "eme", "n": "ene", "o": "o", "p": "pe", "q": "ku", "r": "ere",
    "s": "ese", "t": "te", "u": "u", "v": "ve", "w": "we", "x": "eksi",
    "y": "ya", "z": "ze",
}
ABBREVIATIONS = {
    r"\bDkt\.": "Daktari", r"\bBw\.": "Bwana", r"\bBi\.": "Bibi",
    r"\bProf\.": "Profesa", r"\bNa\.": "namba",
}
ENGLISH_ACRONYMS = {"KDE", "ISBN", "QR", "USB", "OK", "TET", "UDSM", "UDOM", "SQA", "SUA", "ARU", "DUCE", "MARUCO"}
BLANK_TOKEN = re.compile(r"\[\[blank:[^]]+\]\]", re.IGNORECASE)
URL_OR_EMAIL = re.compile(
    r"https?://[^\s,;]+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"\b(?:(?:www|ol)\.)?tie\.go\.tz(?:/[^\s,;]+)?|"
    r"\b(?:ISBN\s*:\s*[0-9-]+|FOR ONLINE READING ONLY|Room to Read)\b",
    re.IGNORECASE,
)
SPECIAL_ENGLISH = re.compile(
    URL_OR_EMAIL.pattern + r"|\b(?:KDE|QR|USB|OK|TET|UDSM|UDOM|SQA|SUA|ARU|DUCE|MARUCo)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpeechSegment:
    voice: str
    text: str


def number_to_swahili(value: int) -> str:
    """Return a natural Swahili reading for a non-negative integer."""
    if value < 10:
        return ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return TENS[tens] + (f" na {ONES[ones]}" if ones else "")
    if value < 1_000:
        hundreds, rest = divmod(value, 100)
        result = f"mia {ONES[hundreds]}"
        return result + (f" na {number_to_swahili(rest)}" if rest else "")
    if value < 1_000_000:
        thousands, rest = divmod(value, 1_000)
        prefix = "elfu moja" if thousands == 1 else f"elfu {number_to_swahili(thousands)}"
        return prefix + (f" na {number_to_swahili(rest)}" if rest else "")
    return str(value)


def ordinal_to_swahili(value: int) -> str:
    return ORDINALS.get(value, number_to_swahili(value))


def spell_english_token(token: str) -> str:
    """Make English/acronym/Internet spans explicit for Daudi."""
    stripped = token.strip()
    if stripped.upper() == "TET":
        # This acronym is conventionally pronounced as one Swahili word.
        return "teti"
    if stripped.upper() == "SQA":
        # Use explicit English letter names so Q cannot be mistaken for T.
        return "es, kyu, ei"
    if stripped.upper().startswith("ISBN"):
        digits = re.sub(r"\D", "", stripped.split(":", 1)[-1])
        return "I S B N, " + ", ".join(ONES[int(digit)] for digit in digits)
    if re.match(r"https?://", stripped, re.I):
        value = re.sub(r"^https", "H T T P S", stripped, flags=re.I)
        value = re.sub(r"^http", "H T T P", value, flags=re.I)
        value = value.replace(":", " colon ").replace("/", " slash ")
        value = value.replace(".", " dot ").replace("-", " hyphen ")
        value = re.sub(r"\bol\b", "O L", value, flags=re.I)
        value = re.sub(r"\btie\b", "T I E", value, flags=re.I)
        value = re.sub(r"\bgo\b", "G O", value, flags=re.I)
        value = re.sub(r"\btz\b", "T Z", value, flags=re.I)
        return re.sub(r"\s+", " ", value).strip()
    if re.match(r"(?:(?:www|ol)\.)?tie\.go\.tz", stripped, re.I):
        value = stripped.replace("/", " slash ").replace(".", " dot ").replace("-", " hyphen ")
        value = re.sub(r"\bwww\b", "W W W", value, flags=re.I)
        value = re.sub(r"\bol\b", "O L", value, flags=re.I)
        value = re.sub(r"\btie\b", "T I E", value, flags=re.I)
        value = re.sub(r"\bgo\b", "G O", value, flags=re.I)
        value = re.sub(r"\btz\b", "T Z", value, flags=re.I)
        return re.sub(r"\s+", " ", value).strip()
    if "@" in stripped:
        value = stripped.replace("@", " at ").replace(".", " dot ").replace("-", " hyphen ")
        value = re.sub(r"\btie\b", "T I E", value, flags=re.I)
        value = re.sub(r"\bgo\b", "G O", value, flags=re.I)
        value = re.sub(r"\btz\b", "T Z", value, flags=re.I)
        return re.sub(r"\s+", " ", value).strip()
    if stripped.upper() in ENGLISH_ACRONYMS:
        return " ".join(stripped.upper())
    return stripped


def _remove_repeated_bracketed_digits(text: str) -> str:
    number_words = "|".join(ONES[1:] + TENS[1:])
    return re.sub(rf"\b({number_words})\s*\(\s*\d+\s*\)", r"\1", text, flags=re.I)


def _replace_numbered_headings(text: str) -> str:
    def activity(match: re.Match[str]) -> str:
        return f"{match.group(1)} {ordinal_to_swahili(int(match.group(2)))}"

    text = re.sub(
        r"\b(Zoezi la|Jaribio la|Kazi ya kufanya ya|Mfano wa)\s+(\d+)\b",
        activity,
        text,
    )
    text = re.sub(
        r"\bKielelezo\s+(\d+)\b",
        lambda m: f"Kielelezo cha {ordinal_to_swahili(int(m.group(1)))}",
        text,
    )
    return text


def _replace_roman_numerals(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = ROMAN_NUMERALS[match.group(0).upper()]
        return number_to_swahili(value)

    return re.sub(r"(?<![\w])(?:III|IV|VI|II|V|I)(?![\w])", replace, text, flags=re.I)


def _replace_ranges_in_prose(text: str) -> str:
    """Read digit hyphens as ranges in prose, but retain subtraction formulas."""
    if not re.search(r"[A-Za-zÀ-ÿ]", text):
        return text
    # A compact 1-9 form is a range; spaced 1 - 9 forms in this mathematics
    # book are subtraction expressions, including those embedded in prose.
    return re.sub(r"(?<![\w])([0-9]+)[-–−]([0-9]+)(?![\w])", r"\1 hadi \2", text)


def spoken_swahili(text: str) -> str:
    """Transform authoritative visible text into Tanzanian Swahili narration."""
    spoken = BLANK_TOKEN.sub(" ", text)
    # In these exercise instructions the slash presents two equivalent verbs;
    # it is punctuation, not the mathematical division operator.
    spoken = re.sub(r"\bSoma\s*/\s*Tambua\b", "Soma au tambua", spoken, flags=re.I)
    spoken = re.sub(r"\bHesabu\s*/\s*Tambua\b", "Hesabu au tambua", spoken, flags=re.I)
    spoken = re.sub(
        r"\bHesabu\s*/\s*kwa lugha ya alama\b",
        "Hesabu kwa lugha ya alama",
        spoken,
        flags=re.I,
    )
    spoken = _remove_repeated_bracketed_digits(spoken)
    spoken = _replace_numbered_headings(spoken)
    spoken = re.sub(r"\ba\s+mpaka\s+d\b", "a mpaka de", spoken, flags=re.I)
    for pattern, replacement in ABBREVIATIONS.items():
        spoken = re.sub(pattern, replacement, spoken)
    spoken = spoken.replace("→", " inaelekea ").replace("←", " inatoka ")
    # In song lyrics, the printed notation ``x 2`` means repeat twice rather
    # than the name of the letter x.
    spoken = re.sub(r"(?<![\w])x\s*(?=\d)", " mara ", spoken, flags=re.I)
    spoken = _replace_roman_numerals(spoken)
    spoken = _replace_ranges_in_prose(spoken)
    spoken = spoken.replace("−", " - ").replace("–", " - ")
    operators = {
        "+": " jumlisha ", "-": " toa ", "=": " ni sawa na ",
        "×": " zidisha kwa ", "÷": " gawanya kwa ", "/": " gawanya kwa ",
    }
    for symbol, words in operators.items():
        spoken = spoken.replace(symbol, words)

    def replace_number(match: re.Match[str]) -> str:
        return number_to_swahili(int(match.group(0).replace(",", "")))

    spoken = re.sub(r"(?<![\w])\d{1,6}(?:,\d{3})*(?![\w])", replace_number, spoken)
    spoken = re.sub(r"\bTEHAMA\b", "tehama", spoken, flags=re.I)
    # Separate the syllables after operator expansion so the hyphen remains a
    # pronunciation cue and is not interpreted as subtraction.
    spoken = re.sub(r"\bpasi\b", "pa-si", spoken, flags=re.I)
    spoken = re.sub(r"\s+", " ", spoken).strip(" ,")
    return spoken


def page_two_spoken(text: str) -> str:
    """Prepare one uninterrupted Daudi utterance for a page-two text item."""
    stripped = BLANK_TOKEN.sub(" ", text).strip()
    # The slash between the two telephone numbers is punctuation, not a
    # division operator. Name the mark so the contact line is unambiguous.
    stripped = re.sub(r"(?<=\d)\s*/\s*(?=\+?\d)", " alama ya mkato ", stripped)
    parts: list[str] = []
    cursor = 0
    for match in SPECIAL_ENGLISH.finditer(stripped):
        before = spoken_swahili(stripped[cursor:match.start()])
        if before:
            parts.append(before)
        parts.append(spell_english_token(match.group(0)))
        cursor = match.end()
    tail = spoken_swahili(stripped[cursor:])
    if tail:
        parts.append(tail)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def toc_page_spoken(text_id: str) -> str | None:
    base_id = text_id[:-10] if text_id.endswith("_easy_read") else text_id
    page_number = TOC_PAGE_NUMBERS.get(base_id)
    if page_number is None:
        return None
    if base_id in TOC_ROMAN_PAGE_NUMBERS:
        return f"namba {number_to_swahili(page_number)} ya Kirumi"
    return f"ukurasa wa {ordinal_to_swahili(page_number) if page_number == 1 else number_to_swahili(page_number)}"


def speech_segments(text_id: str, text: str) -> tuple[SpeechSegment, ...]:
    """Transform content into speech segments that all use Daudi."""
    base_id = text_id[:-10] if text_id.endswith("_easy_read") else text_id
    numbered_heading = re.fullmatch(
        r"\s*(Zoezi\s+la|Shughuli\s+ya|Kazi\s+ya)\s+(\d+)\s*[.]?\s*",
        text,
        re.IGNORECASE,
    )
    if numbered_heading:
        heading, number = numbered_heading.groups()
        return (
            SpeechSegment(
                DEFAULT_VOICE,
                f"{heading} {ordinal_to_swahili(int(number))}",
            ),
        )
    if base_id == "pg033_n0031":
        # Keep the compact slash in print, but pronounce it naturally as “au”.
        return (SpeechSegment(DEFAULT_VOICE, "Soma au Sikiliza habari hii"),)
    if base_id in {
        "pg035_n0024", "pg035_n0027",
        "pg036_n0040", "pg036_n0043", "pg036_n0046", "pg036_n0049",
        "pg037_n0154",
        "pg044_n0028",
        "pg096_n0009",
        "pg105_n0003",
        "pg106_n0010",
        "pg122_n0020",
        "pg140_n0012", "pg140_n0014", "pg140_n0016", "pg140_n0018",
        "pg140_n0020", "pg140_n0022", "pg140_n0024",
        "pg144_n0010", "pg144_n0012", "pg144_n0015",
    }:
        # These questions display alternate reading modes with a slash.
        return (SpeechSegment(DEFAULT_VOICE, text.replace(" / ", " au ")),)
    if base_id == "pg129_im018_seg009_v1_crop_v1_crop1":
        # In this lettered shape exercise, lowercase "i" is an alphabet
        # label.  Bypass the general Roman-numeral conversion, which would
        # otherwise turn "Herufi i" into "Herufi moja".
        narration = BLANK_TOKEN.sub(" ", text).strip()
        return (SpeechSegment(DEFAULT_VOICE, narration),)
    if base_id == "pg015_n0010":
        return (SpeechSegment(DEFAULT_VOICE, "Namba zilizochanganywa: " + spoken_swahili(text)),)
    if base_id == "pg015_n0011":
        return (SpeechSegment(DEFAULT_VOICE, "Namba zilizopangwa: " + spoken_swahili(text)),)
    if base_id in {"pg065_n0004", "pg065_n0008", "pg066_n0003"}:
        return (
            SpeechSegment(
                DEFAULT_VOICE,
                "Soma au Tambua namba zifuatazo kwa sauti.",
            ),
        )
    if base_id == "pg051_n0021":
        return (
            SpeechSegment(
                DEFAULT_VOICE,
                "Hatua ya pili. Soma au Tambua swali linaloonekana kwenye skirini na ulielewe.",
            ),
        )
    place_value_narration = PAGE_EIGHTY_THREE_PLACE_VALUE_SPEECH.get(base_id)
    if place_value_narration:
        return (SpeechSegment(DEFAULT_VOICE, place_value_narration),)
    column_narration = PAGE_THIRTEEN_COLUMN_SPEECH.get(base_id)
    if column_narration:
        return (SpeechSegment(DEFAULT_VOICE, column_narration + "."),)
    equation_narration = PAGE_THIRTY_NINE_EQUATION_SPEECH.get(base_id)
    if equation_narration:
        return (SpeechSegment(DEFAULT_VOICE, equation_narration),)
    page_narration = toc_page_spoken(text_id)
    if page_narration:
        return (SpeechSegment(DEFAULT_VOICE, page_narration),)
    stripped = BLANK_TOKEN.sub(" ", text).strip()
    if not re.search(r"[\wÀ-ÿ]", stripped):
        return (SpeechSegment("silence", ""),)
    question_number = re.fullmatch(r"(\d+)\.", stripped)
    if question_number:
        return (
            SpeechSegment(
                DEFAULT_VOICE,
                f"Swali la {ordinal_to_swahili(int(question_number.group(1)))}.",
            ),
        )
    # Some exercise layouts combine the question number and arithmetic
    # expression in one text item (for example, ``1. 10 - 7 = dashi``).
    # Name that leading number as a question, just as we do for a standalone
    # ``1.`` item.  Requiring the remainder to begin with an arithmetic
    # expression avoids mislabelling numbered instructional steps.
    combined_question = re.fullmatch(
        r"(\d+)\.\s*(\d+\s*[+\-−–×÷/].*)",
        stripped,
        flags=re.DOTALL,
    )
    if combined_question:
        transformed = spoken_swahili(combined_question.group(2))
        return (
            SpeechSegment(
                DEFAULT_VOICE,
                f"Swali la {ordinal_to_swahili(int(combined_question.group(1)))}. "
                f"{transformed}",
            ),
        )
    if text_id.startswith("pg002_"):
        transformed = page_two_spoken(stripped)
        if transformed and transformed[-1] not in ".!?":
            transformed += "."
        return (SpeechSegment(DEFAULT_VOICE, transformed),)
    if stripped.lower().rstrip(".):") in LIST_LETTERS and len(stripped) <= 3:
        key = stripped.lower().rstrip(".):")
        return (SpeechSegment(DEFAULT_VOICE, LIST_LETTERS[key] + "."),)

    segments: list[SpeechSegment] = []
    cursor = 0
    for match in SPECIAL_ENGLISH.finditer(stripped):
        before = spoken_swahili(stripped[cursor:match.start()])
        if before and re.search(r"[\wÀ-ÿ]", before):
            segments.append(SpeechSegment(DEFAULT_VOICE, before))
        token = match.group(0)
        segments.append(SpeechSegment(DEFAULT_VOICE, spell_english_token(token)))
        cursor = match.end()
    tail = spoken_swahili(stripped[cursor:])
    if tail and re.search(r"[\wÀ-ÿ]", tail):
        segments.append(SpeechSegment(DEFAULT_VOICE, tail))
    if not segments:
        transformed = spoken_swahili(stripped)
        segments.append(SpeechSegment(DEFAULT_VOICE, transformed) if transformed else SpeechSegment("silence", ""))

    # Punctuation gives isolated labels/table cells a short terminal pause.
    if len(stripped.split()) <= 3:
        last = segments[-1]
        if last.text and last.text[-1] not in ".!?":
            segments[-1] = SpeechSegment(last.voice, last.text + ".")
    return tuple(segment for segment in segments if segment.text.strip() or segment.voice == "silence")


def load_jobs(requested_ids: set[str] | None = None):
    grouped: dict[tuple[SpeechSegment, ...], list[Path]] = defaultdict(list)
    affected: dict[str, dict[str, object]] = {}
    for lang in LANGS:
        base = ROOT / "content" / "i18n" / lang
        texts = json.loads((base / "texts.json").read_text(encoding="utf-8"))
        audios = json.loads((base / "audios.json").read_text(encoding="utf-8"))
        for text_id, filename in audios.items():
            if requested_ids is not None and text_id not in requested_ids:
                continue
            text = texts.get(text_id, "")
            if isinstance(text, str) and text.strip():
                segments = speech_segments(text_id, text)
                grouped[segments].append(base / "audio" / filename)
                affected.setdefault(text_id, {"visible": text, "spoken": [s.text for s in segments], "voices": [s.voice for s in segments]})
    return grouped, affected


def bump_audio_versions(requested_ids: set[str]) -> None:
    """Give regenerated clips fresh filenames so readers cannot reuse old audio."""
    version_pattern = re.compile(r"_v(\d+)\.mp3$")
    for lang in LANGS:
        mapping_path = ROOT / "content" / "i18n" / lang / "audios.json"
        audios = json.loads(mapping_path.read_text(encoding="utf-8"))
        changed = False
        for text_id in requested_ids:
            filename = audios.get(text_id)
            if not filename:
                continue
            match = version_pattern.search(filename)
            if "daudi" not in filename.casefold():
                unversioned_stem = version_pattern.sub("", filename)
                audios[text_id] = f"{Path(unversioned_stem).stem}_daudi_v1.mp3"
            elif not match:
                audios[text_id] = f"{Path(filename).stem}_daudi_v1.mp3"
            else:
                next_version = int(match.group(1)) + 1
                audios[text_id] = version_pattern.sub(f"_v{next_version}.mp3", filename)
            changed = True
        if changed:
            mapping_path.write_text(
                json.dumps(audios, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


async def main() -> None:
    try:
        import edge_tts
    except ModuleNotFoundError as error:
        raise SystemExit("edge-tts is required only when generating new audio clips") from error
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--rate", default=DEFAULT_RATE)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--ids-file", type=Path, help="Read one requested text ID per line")
    parser.add_argument("--matching-regex", help="Generate mapped IDs whose visible text matches this regex")
    parser.add_argument(
        "--legacy-filenames",
        action="store_true",
        help="Generate every mapped ID whose filename is not explicitly versioned as Daudi audio",
    )
    parser.add_argument(
        "--label-regenerated-legacy-as-daudi",
        action="store_true",
        help="Copy freshly regenerated legacy clips to explicit Daudi filenames and update mappings",
    )
    parser.add_argument(
        "--bump-version",
        action="store_true",
        help="Increment the mapped filename version for every requested ID before generation",
    )
    parser.add_argument("--manifest", type=Path, help="Write the transformed-ID manifest")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.label_regenerated_legacy_as_daudi:
        relabeled = 0
        version_pattern = re.compile(r"_v\d+\.mp3$")
        for lang in LANGS:
            base = ROOT / "content" / "i18n" / lang
            mapping_path = base / "audios.json"
            audios = json.loads(mapping_path.read_text(encoding="utf-8"))
            for text_id, filename in list(audios.items()):
                if "daudi" in filename.casefold():
                    continue
                source = base / "audio" / filename
                unversioned = version_pattern.sub("", filename)
                destination_name = f"{Path(unversioned).stem}_daudi_v1.mp3"
                if not source.is_file() or source.stat().st_size == 0:
                    raise SystemExit(f"Missing regenerated source clip: {source}")
                shutil.copy2(source, base / "audio" / destination_name)
                audios[text_id] = destination_name
                relabeled += 1
            mapping_path.write_text(
                json.dumps(audios, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"Relabeled {relabeled} freshly regenerated clips with explicit Daudi filenames.")
        return

    requested_ids = set(args.ids) if args.ids else None
    if args.ids_file:
        requested_ids = {
            line.strip() for line in args.ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    if args.legacy_filenames:
        sw_audios = json.loads(
            (ROOT / "content" / "i18n" / "sw" / "audios.json").read_text(encoding="utf-8")
        )
        legacy_ids = {
            text_id for text_id, filename in sw_audios.items()
            if "daudi" not in filename.casefold()
        }
        requested_ids = legacy_ids if requested_ids is None else requested_ids | legacy_ids
    if args.matching_regex:
        sw_base = ROOT / "content" / "i18n" / "sw"
        sw_texts = json.loads((sw_base / "texts.json").read_text(encoding="utf-8"))
        sw_audios = json.loads((sw_base / "audios.json").read_text(encoding="utf-8"))
        pattern = re.compile(args.matching_regex)
        matched = {
            text_id for text_id, value in sw_texts.items()
            if text_id in sw_audios and isinstance(value, str) and pattern.search(value)
        }
        requested_ids = matched if requested_ids is None else requested_ids | matched
    if args.bump_version:
        if requested_ids is None:
            raise SystemExit("--bump-version requires --ids, --ids-file, or --matching-regex")
        bump_audio_versions(requested_ids)
    jobs, affected = load_jobs(requested_ids)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(affected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    items = sorted(jobs.items(), key=lambda item: repr(item[0]))
    if args.limit:
        items = items[:args.limit]
    if args.dry_run:
        print(f"Prepared {len(affected)} IDs in {len(items)} unique segmented phrases.")
        return

    semaphore = asyncio.Semaphore(args.concurrency)
    cache_dir = Path(tempfile.mkdtemp(prefix="kuhesabu-tts-"))

    async def synthesize(segment: SpeechSegment, destination: Path) -> None:
        if segment.voice == "silence":
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                "anullsrc=r=24000:cl=mono", "-t", "0.35", "-q:a", "9",
                "-acodec", "libmp3lame", str(destination),
            )
            if await process.wait() != 0:
                raise RuntimeError("ffmpeg failed while creating a silent answer-field clip")
            return
        voice = args.voice
        error: Exception | None = None
        for attempt in range(4):
            try:
                await asyncio.wait_for(
                    edge_tts.Communicate(segment.text, voice, rate=args.rate).save(str(destination)),
                    timeout=30,
                )
                return
            except Exception as exc:
                error = exc
                await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"TTS failed after retries: {segment.text[:80]!r}") from error

    async def generate(segments: tuple[SpeechSegment, ...], destinations: list[Path]) -> None:
        digest = hashlib.sha256(repr(segments).encode("utf-8")).hexdigest()
        cached = cache_dir / f"{digest}.mp3"
        async with semaphore:
            pieces: list[Path] = []
            for index, segment in enumerate(segments):
                piece = cache_dir / f"{digest}-{index}.mp3"
                await synthesize(segment, piece)
                pieces.append(piece)
            if len(pieces) == 1:
                shutil.copyfile(pieces[0], cached)
            else:
                concat_file = cache_dir / f"{digest}.txt"
                concat_file.write_text("".join(f"file '{piece}'\n" for piece in pieces), encoding="utf-8")
                process = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat_file), "-c", "copy", str(cached),
                )
                if await process.wait() != 0:
                    raise RuntimeError(f"ffmpeg failed while joining {len(pieces)} speech segments")
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, destination)

    try:
        await asyncio.gather(*(generate(segments, destinations) for segments, destinations in items))
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)

    clip_count = sum(len(destinations) for _, destinations in items)
    print(f"Generated {clip_count} clips from {len(items)} unique phrases with {args.voice} at {args.rate}.")


if __name__ == "__main__":
    asyncio.run(main())
