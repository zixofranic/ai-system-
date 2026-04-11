"""
Forced alignment of Whisper word timestamps to ground-truth script text.

Whisper is great at TIMING but sometimes mis-transcribes uncommon proper
nouns (e.g. "Saya" → "Sire", "Elias" → "Alliance"). The original story
script contains the correct spelling — we just need to re-label Whisper's
timed tokens with the right words.

This module uses difflib.SequenceMatcher to align the two token streams
and rewrite each word's text field while keeping its start/end times.
"""

import difflib
import re
from typing import List, Dict


_PUNCT_RE = re.compile(r"[.,!?;:\"'\u2018\u2019\u201c\u201d\u2014\u2013]+")


def _normalize(token: str) -> str:
    """Strip punctuation and lowercase for comparison."""
    return _PUNCT_RE.sub("", token.lower()).strip()


def align_whisper_to_script(
    whisper_words: List[Dict],
    script_text: str,
) -> List[Dict]:
    """
    Re-label Whisper's word list with ground-truth words from the script,
    preserving Whisper's start/end timestamps via sequence alignment.

    Args:
        whisper_words: List of dicts with keys 'word', 'start', 'end'
                       (as returned by whisper.transcribe with word_timestamps)
        script_text: The original script text (ground truth spelling)

    Returns:
        New list of dicts with corrected 'word' values and original timings.
    """
    if not whisper_words:
        return []

    script_tokens = script_text.split()
    script_norms = [_normalize(t) for t in script_tokens]

    whisper_norms = [_normalize(w["word"]) for w in whisper_words]

    # Align the two normalized sequences
    matcher = difflib.SequenceMatcher(
        None, whisper_norms, script_norms, autojunk=False,
    )

    result: List[Dict] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Whisper nailed these tokens — keep as-is, but use the script's
            # spelling (which preserves proper capitalization / hyphenation)
            for k, w_idx in enumerate(range(i1, i2)):
                src_idx = j1 + k
                result.append({
                    "word": script_tokens[src_idx],
                    "start": whisper_words[w_idx]["start"],
                    "end": whisper_words[w_idx]["end"],
                })

        elif tag == "replace":
            # Whisper mis-transcribed these — substitute script tokens
            whisper_chunk = whisper_words[i1:i2]
            script_chunk = script_tokens[j1:j2]

            if not whisper_chunk:
                continue

            if not script_chunk:
                # Whisper hallucinated — drop these tokens
                continue

            if len(whisper_chunk) == len(script_chunk):
                # 1:1 replace, keep each original timing
                for k, src in enumerate(script_chunk):
                    result.append({
                        "word": src,
                        "start": whisper_chunk[k]["start"],
                        "end": whisper_chunk[k]["end"],
                    })
            else:
                # Different counts — spread script tokens across the
                # total Whisper time range for this chunk
                t_start = whisper_chunk[0]["start"]
                t_end = whisper_chunk[-1]["end"]
                span = max(t_end - t_start, 0.01)
                per = span / len(script_chunk)
                for k, src in enumerate(script_chunk):
                    result.append({
                        "word": src,
                        "start": t_start + k * per,
                        "end": t_start + (k + 1) * per,
                    })

        elif tag == "insert":
            # Script has tokens Whisper didn't hear (rare) — borrow a tiny
            # sliver of time from the next Whisper word, or piggyback on the
            # previous one.
            script_chunk = script_tokens[j1:j2]
            if not script_chunk:
                continue
            # Pick a reference time: end of previous result, or start of
            # the next whisper word
            ref_start = result[-1]["end"] if result else (
                whisper_words[i1]["start"] if i1 < len(whisper_words) else 0.0
            )
            ref_end = (
                whisper_words[i2]["start"]
                if i2 < len(whisper_words)
                else ref_start + 0.2 * len(script_chunk)
            )
            span = max(ref_end - ref_start, 0.05 * len(script_chunk))
            per = span / len(script_chunk)
            for k, src in enumerate(script_chunk):
                result.append({
                    "word": src,
                    "start": ref_start + k * per,
                    "end": ref_start + (k + 1) * per,
                })

        elif tag == "delete":
            # Whisper added words that aren't in the script — drop them
            # (false positives; keeping them would desync captions)
            continue

    # Safety: ensure monotonic timestamps
    for i in range(1, len(result)):
        if result[i]["start"] < result[i - 1]["end"]:
            result[i]["start"] = result[i - 1]["end"]
        if result[i]["end"] < result[i]["start"]:
            result[i]["end"] = result[i]["start"] + 0.05

    return result


if __name__ == "__main__":
    # Quick sanity check
    whisper_in = [
        {"word": "the", "start": 0.0, "end": 0.2},
        {"word": "jar", "start": 0.2, "end": 0.5},
        {"word": "is", "start": 0.5, "end": 0.6},
        {"word": "on", "start": 0.6, "end": 0.7},
        {"word": "the", "start": 0.7, "end": 0.8},
        {"word": "table", "start": 0.8, "end": 1.1},
        {"word": "when", "start": 1.1, "end": 1.3},
        {"word": "sire", "start": 1.3, "end": 1.6},  # ← mis-transcribed
        {"word": "wakes", "start": 1.6, "end": 1.9},
    ]
    script = "The jar is on the kitchen table when Saya wakes"
    out = align_whisper_to_script(whisper_in, script)
    for w in out:
        print(f"  {w['word']:10} {w['start']:.2f}-{w['end']:.2f}")
