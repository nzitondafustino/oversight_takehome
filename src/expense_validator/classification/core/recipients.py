"""Conservative recipient evidence from descriptions only; never merchant context."""

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Recipient evidence in a line's own words: a relationship cue, then a person. Conservative on purpose,
# so it reads no capitalised product or menu words as people. A person is an initial and surname
# (R. Delgado), a title and a name (Deputy Mayor T. Nguyen, Sr. Ruiz), or, only after a clear guest or
# client cue, a full name (guest Maria Lopez). Japanese names carry an honorific (田中様).
_INITIALS_NAME = r"(?:[A-Z]\.\s?){1,2}[A-Z][\w'’-]+"
_TITLED_NAME = (
    r"(?:Mr|Mrs|Ms|Mx|Dr|Prof|Sr|Sra|Mme|Herr|Frau)\.?\s+[A-Z]"
    r"|(?:Deputy\s+)?(?:Senator|Mayor|Minister|Judge|Officer|Ambassador|Councillor|Governor|Secretary|Commissioner)\s+[A-Z]"
)
_FULL_NAME = r"[A-Z][a-zà-ÿ'’-]+(?:\s[A-Z][a-zà-ÿ'’-]+){1,2}\b"
_PERSON_CUES = r"guests?|attendees?|clients?|customers?|employees?|colleagues?|invitad[oa]s?|invitée?s?|clientes?|gast|gäste|kunden?|mitarbeiter(?:in)?|convidad[oa]s?"
_LINKS = r"for|with|attn|para|con|pour|avec|für|mit|com"
_ROLE_WORDS = r"(?:[a-zà-ÿ]+\s+){0,2}"
RECIPIENT_CUE = re.compile(
    rf"\b(?i:{_PERSON_CUES}|{_LINKS})\b\s*[-:–—,]?\s*{_ROLE_WORDS}(?:{_INITIALS_NAME}|{_TITLED_NAME})"  # for R. Delgado
    rf"|\b(?:{_PERSON_CUES})\b\s+{_ROLE_WORDS}{_FULL_NAME}"  # guest Maria Lopez, with the cue in lower case
    rf"|(?:[-:–—,]|\b(?i:{_LINKS})\b)\s*\b(?i:{_PERSON_CUES})\b\s*[-:–—]?\s*{_FULL_NAME}"  # mit Gast Anna Weber
    rf"|\b(?i:{_PERSON_CUES})\b\s*[-:–—]\s*{_FULL_NAME}"  # Guest: Maria Lopez
    r"|[぀-ヿ一-鿿](?:様|さん|氏)"  # 田中様
)
# Products whose names look like a person after a cue word.
NOT_PEOPLE = re.compile(r"\b(?:Dr\.?\s?Pepper|Mr\.?\s?Clean|Dr\.?\s?Martens|Mrs\.?\s?Meyer'?s|J\.\s?Lohr)\b", re.IGNORECASE)


class RecipientMention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    source: Literal["description_normalized", "translation"]
    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def span(self):
        if self.end - self.start != len(self.text):
            raise ValueError("recipient span does not match text length")
        return self


def names_a_recipient(*texts: str | None) -> bool:
    return any(text and RECIPIENT_CUE.search(NOT_PEOPLE.sub(" ", text)) for text in texts)


# Only complete names are extracted. The older presence detector intentionally accepts weaker cues.
_NAME = re.compile(rf"{_INITIALS_NAME}|(?:Mr|Mrs|Ms|Mx|Dr|Prof|Sr|Sra|Mme|Herr|Frau)\.?\s+[A-Z][\w'’-]+|{_FULL_NAME}|[぀-ヿ一-鿿]+(?=様|さん|氏)")


def recipient_mentions(original: str, translation: str | None = None) -> tuple[RecipientMention, ...]:
    found = []
    for source, text in (("description_normalized", original), ("translation", translation)):
        if not text:
            continue
        # Mask brands with equal-length spaces, preserving source offsets.
        masked = NOT_PEOPLE.sub(lambda m: " " * len(m[0]), text)
        for cue in RECIPIENT_CUE.finditer(masked):
            # Presence patterns for titles may end after the surname's first letter.
            cue_start = cue.start()
            if re.match(r"[぀-ヿ一-鿿]", masked[cue_start:]):
                while cue_start > 0 and re.fullmatch(r"[぀-ヿ一-鿿]", masked[cue_start-1]):
                    cue_start -= 1
            tail = masked[cue_start:]
            stop = re.search(r"[,;\n]", tail)
            segment = tail[:stop.start()] if stop else tail
            candidates = list(_NAME.finditer(segment))
            if not candidates:
                continue
            previous_end = None
            for match in candidates:
                if re.fullmatch(r"(?:(?:Deputy|Senator|Mayor|Minister|Judge|Officer|Ambassador|Councillor|Governor|Secretary|Commissioner)\s*)+", match[0]):
                    continue
                if previous_end is not None and not re.fullmatch(r"\s+(?:and|et|und|y|e|&)\s+", segment[previous_end:match.start()]):
                    break
                previous_end = match.end()
                start = cue_start + match.start()
                prefix = re.match(rf"(?i:{_PERSON_CUES})\s+", match[0])
                if prefix:
                    start += prefix.end()
                end = cue_start + match.end()
                found.append(RecipientMention(source=source, text=text[start:end], start=start, end=end))
    return tuple(found)
