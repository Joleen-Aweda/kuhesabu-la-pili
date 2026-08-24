#!/usr/bin/env python3
"""Audit Swahili read-aloud catalogs, files, HTML IDs, and speech transforms."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

from regenerate_tanzanian_audio import DEFAULT_VOICE, LANGS, ROOT, speech_segments


class BundleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_ids: list[tuple[str, str]] = []
        self.images: list[tuple[str, str, str, str, bool]] = []

    def handle_starttag(self, tag, attrs):
        values = {key: value or "" for key, value in attrs}
        text_id = values.get("data-id", "").strip()
        if text_id:
            self.text_ids.append((tag, text_id))
        if tag == "img":
            self.images.append((
                text_id,
                values.get("src", ""),
                values.get("alt", ""),
                values.get("data-visual-id", "").strip(),
                values.get("aria-hidden", "").casefold() == "true",
            ))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tmp" / "read-aloud")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    catalogs = {}
    errors: list[str] = []
    for lang in LANGS:
        base = ROOT / "content" / "i18n" / lang
        texts = json.loads((base / "texts.json").read_text(encoding="utf-8"))
        audios = json.loads((base / "audios.json").read_text(encoding="utf-8"))
        catalogs[lang] = (texts, audios, base / "audio")
        for text_id, filename in audios.items():
            path = base / "audio" / filename
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"{lang}:{text_id}: missing or empty {filename}")
            if text_id not in texts or not isinstance(texts[text_id], str):
                errors.append(f"{lang}:{text_id}: mapping has no text")

    sw_texts, sw_audios, sw_audio_dir = catalogs["sw"]
    tz_texts, tz_audios, tz_audio_dir = catalogs["sw-TZ"]
    if sw_texts != tz_texts:
        errors.append("sw and sw-TZ texts.json differ")
    if sw_audios != tz_audios:
        errors.append("sw and sw-TZ audios.json differ")
    for text_id, filename in sw_audios.items():
        left, right = sw_audio_dir / filename, tz_audio_dir / filename
        if left.is_file() and right.is_file() and sha256(left) != sha256(right):
            errors.append(f"{text_id}: sw and sw-TZ audio bytes differ")

    easy_ids = {key for key in sw_audios if key.endswith("_easy_read")}
    for easy_id in easy_ids:
        base_id = easy_id[:-10]
        if base_id not in sw_audios:
            errors.append(f"{easy_id}: easy-read mapping has no normal mapping")

    html_files = [ROOT / "index.html", *sorted(ROOT.glob("pg*.html")), *sorted(ROOT.glob("qz*.html"))]
    image_instances = 0
    duplicate_image_instances = 0
    html_text_ids: set[str] = set()
    image_group_rows: list[dict[str, object]] = []
    for page in html_files:
        parsed = BundleParser()
        parsed.feed(page.read_text(encoding="utf-8"))
        html_text_ids.update(text_id for _, text_id in parsed.text_ids)
        image_instances += len(parsed.images)
        counts = Counter(text_id for text_id, _, _, _, _ in parsed.images if text_id)
        duplicate_image_instances += sum(count - 1 for count in counts.values() if count > 1)
        for text_id, count in sorted(counts.items()):
            if count > 1:
                image_group_rows.append({"page": page.name, "text_id": text_id, "instances": count})
        page_source = page.read_text(encoding="utf-8")
        for text_id, _, alt, visual_id, aria_hidden in parsed.images:
            if not text_id:
                if aria_hidden and not alt:
                    continue
                if visual_id and f'data-id="{visual_id}"' in page_source:
                    continue
                errors.append(f"{page.name}: image without data-id")
                continue
            if text_id not in sw_texts or not sw_texts[text_id].strip():
                errors.append(f"{page.name}:{text_id}: image missing description")
            elif alt != sw_texts[text_id]:
                errors.append(f"{page.name}:{text_id}: alt differs from printed description catalog")

    # IDs used only as section/activity containers are allowed to have no text.
    missing_html_audio = sorted(
        text_id for text_id in html_text_ids
        if text_id in sw_texts
        and sw_texts[text_id].strip()
        and not re.fullmatch(r"[_\s]+", sw_texts[text_id])
        and text_id not in sw_audios
    )
    errors.extend(f"{text_id}: visible text has no audio mapping" for text_id in missing_html_audio)

    rules: dict[str, list[str]] = defaultdict(list)
    all_ids = sorted(sw_audios)
    for text_id in all_ids:
        visible = sw_texts[text_id]
        segments = speech_segments(text_id, visible)
        spoken = " | ".join(segment.text for segment in segments)
        unexpected_voices = {
            segment.voice for segment in segments
            if segment.voice not in {DEFAULT_VOICE, "silence"}
        }
        if unexpected_voices:
            errors.append(
                f"{text_id}: non-Daudi speech voices: {sorted(unexpected_voices)}"
            )
        tests = {
            "answer_field_removed": r"\[\[blank:",
            "abbreviation_expanded": r"\b(?:Dkt|Bw|Bi|Prof|Na)\.",
            "numbered_heading_ordinal": r"\b(?:Zoezi la|Jaribio la|Kazi ya kufanya ya|Mfano wa|Kielelezo)\s+\d+",
            "mixed_english_segment": r"https?://|@|\b(?:ISBN|KDE|QR|USB|OK|TET|UDSM|UDOM|SQA)\b|FOR ONLINE READING ONLY|Room to Read",
            "arrow_expanded": r"[→←]",
            "list_letter_expanded": r"^(?:a|b|c|d)[\).:]?$",
            "roman_numeral_expanded": r"^(?:i|ii|iii|iv|v|vi)$",
            "standalone_question_number": r"^\s*\d+\.\s*$",
            "bracketed_digit_deduplicated": r"\([0-9]+\)",
        }
        for name, pattern in tests.items():
            if re.search(pattern, visible, re.I):
                rules[name].append(text_id)
        if visible.strip() != spoken.strip():
            rules["spoken_text_differs_from_visible"].append(text_id)
        if re.fullmatch(r"\s*\d+\.\s*", visible) and not spoken.startswith("Swali la "):
            errors.append(f"{text_id}: question number does not begin with 'Swali la'")

    human_review = sorted({
        text_id for text_id, text in sw_texts.items()
        if text_id in sw_audios and (
            re.search(r"\b(?:Room to Read|[A-Z][a-z]+\s+[A-Z]\.|https?://|@)", text)
            or (len(text.split()) <= 2 and re.search(r"[A-Za-zÀ-ÿ]", text))
        )
    })

    (args.output_dir / "affected_text_ids.txt").write_text("\n".join(all_ids) + "\n", encoding="utf-8")
    (args.output_dir / "human_listening_review_ids.txt").write_text("\n".join(human_review) + "\n", encoding="utf-8")
    (args.output_dir / "human_listening_review.json").write_text(
        json.dumps(
            {
                text_id: {
                    "visible": sw_texts[text_id],
                    "spoken_segments": [segment.text for segment in speech_segments(text_id, sw_texts[text_id])],
                    "voices": [segment.voice for segment in speech_segments(text_id, sw_texts[text_id])],
                }
                for text_id in human_review
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "image_group_instances.json").write_text(
        json.dumps(image_group_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "texts_per_locale": len(sw_texts),
        "mapped_audio_ids": len(sw_audios),
        "easy_read_audio_ids": len(easy_ids),
        "html_files": len(html_files),
        "html_data_ids": len(html_text_ids),
        "image_instances": image_instances,
        "duplicate_image_instances_consolidated_at_runtime": duplicate_image_instances,
        "rule_counts": {name: len(ids) for name, ids in sorted(rules.items())},
        "human_listening_review_count": len(human_review),
        "errors": errors,
    }
    (args.output_dir / "audit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "rule-affected-ids.json").write_text(
        json.dumps(rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
