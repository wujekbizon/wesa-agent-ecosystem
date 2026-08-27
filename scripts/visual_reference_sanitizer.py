import re
from dataclasses import dataclass


VISUAL_WORD = (
    r"(?:tabel(?:a|i|ą|ę|e)|tablic(?:a|y|ą|ę|ach)|"
    r"rycin(?:a|y|ą|ę|ie)|rysunk(?:u|iem|i|ach)|"
    r"ilustracj(?:a|i|ą|ę))"
)
QUALIFIER = (
    r"(?:(?:podan(?:ej|ą)|zamieszczon(?:ej|ą)|załączon(?:ej|ą)|"
    r"poniższ(?:ej|ą)|powyższ(?:ej|ą))\s+)?"
)
LABEL = r"(?:\s+(?:nr\s*)?(?:\d+(?:\.\d+)*|[IVXLC]+))?"

PREFIX_WITH_COMMA = re.compile(
    rf"^\s*(?:na podstawie|zgodnie z|według|w odniesieniu do)\s+"
    rf"{QUALIFIER}{VISUAL_WORD}\b[^,?!]{{0,160}},\s*",
    re.IGNORECASE,
)
REFERENCE_INTRO = (
    r"(?:zgodnie z|według|na podstawie|w odniesieniu do|"
    r"jak (?:podano|przedstawiono|pokazano)(?:\s+w)?)"
)
MID_REFERENCE = re.compile(
    rf"\s*,\s*{REFERENCE_INTRO}\s+{QUALIFIER}{VISUAL_WORD}{LABEL}[^,?!]{{0,80}},\s*",
    re.IGNORECASE,
)
TRAILING_REFERENCE = re.compile(
    rf"\s*,?\s*{REFERENCE_INTRO}\s+{QUALIFIER}{VISUAL_WORD}{LABEL}\s*([?.!])$",
    re.IGNORECASE,
)
PARENTHETICAL_SOURCE = re.compile(
    rf"\s*\(\s*{REFERENCE_INTRO}\s+{QUALIFIER}{VISUAL_WORD}[^)]{{0,160}}\)",
    re.IGNORECASE,
)
PARENTHETICAL_REFERENCE = re.compile(
    rf"\s*\(\s*{VISUAL_WORD}{LABEL}\s*\)", re.IGNORECASE
)
SOURCE_SENTENCE = re.compile(
    rf"^\s*(?:poniższ[ay]|powyższ[ay]|załączon[ay])?\s*{VISUAL_WORD}{LABEL}\s+"
    rf"(?:przedstawia|pokazuje|obrazuje)\b[^?.!]{{0,240}}[.:;]\s*",
    re.IGNORECASE,
)
MENTIONED_IN_VISUAL = re.compile(
    rf"\s*,?\s*(?:wymienion\w*|przedstawion\w*|pokazan\w*)\s+w\s+"
    rf"{QUALIFIER}{VISUAL_WORD}{LABEL}\s*,?",
    re.IGNORECASE,
)
IN_TABLE_LABEL = re.compile(rf"\s+w\s+tabeli{LABEL}\b", re.IGNORECASE)
ACCORDING_TO_TABLE_LIST = re.compile(
    r"\s*,?\s*zgodnie z wykazem z tabeli\s*,?", re.IGNORECASE
)
VISUAL_TOKEN = re.compile(rf"\b{VISUAL_WORD}\b", re.IGNORECASE)
LEGITIMATE_TABLICA = re.compile(
    r"\btablic\w*\s+(?:Snellena|Ishihary)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class SanitizationResult:
    original: str
    sanitized: str
    changed: bool
    residual_visual_reference: bool


def _clean_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    text = re.sub(r",\s*([?.!])", r"\1", text)
    if text and text[0].isalpha():
        text = text[0].upper() + text[1:]
    return text


def has_residual_visual_reference(text: str) -> bool:
    without_legitimate_instruments = LEGITIMATE_TABLICA.sub("", text)
    return VISUAL_TOKEN.search(without_legitimate_instruments) is not None


def sanitize_question(text: str) -> SanitizationResult:
    sanitized = text
    sanitized = SOURCE_SENTENCE.sub("", sanitized)
    sanitized = PREFIX_WITH_COMMA.sub("", sanitized)
    sanitized = PARENTHETICAL_SOURCE.sub("", sanitized)
    sanitized = PARENTHETICAL_REFERENCE.sub("", sanitized)
    sanitized = MID_REFERENCE.sub(" ", sanitized)
    sanitized = TRAILING_REFERENCE.sub(r"\1", sanitized)
    sanitized = MENTIONED_IN_VISUAL.sub("", sanitized)
    sanitized = ACCORDING_TO_TABLE_LIST.sub("", sanitized)
    sanitized = re.sub(r"\bw tabeli oceny\b", "w ocenie", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(
        r"\bw tabeli klasyfikacji\b", "w klasyfikacji", sanitized, flags=re.IGNORECASE
    )
    sanitized = re.sub(
        r"\bw tabeli dokumentacji\b", "w dokumentacji", sanitized, flags=re.IGNORECASE
    )
    sanitized = re.sub(r"\bw tabeli wyników\b", "w wynikach", sanitized, flags=re.IGNORECASE)
    sanitized = IN_TABLE_LABEL.sub("", sanitized)
    if sanitized != text:
        sanitized = _clean_spacing(sanitized)
    return SanitizationResult(
        original=text,
        sanitized=sanitized,
        changed=sanitized != text,
        residual_visual_reference=has_residual_visual_reference(sanitized),
    )
