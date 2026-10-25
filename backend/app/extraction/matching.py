"""Deterministic normalization used only for matching, never for display.

Two separate concerns live here:

* Entity match keys (``name_key``, ``tax_id_key``, ``party_match_key``) decide whether
  two extracted parties are the *same* graph node. They are exact keys: no fuzzy or
  embedding similarity is ever used to merge entities.
* Evidence match forms (``evidence_form``, ``ocr_fold``) compare a model's quoted
  evidence with parser/OCR text while tolerating layout whitespace, punctuation, line
  wraps, Unicode compatibility forms, locale number formatting and common OCR
  character substitutions. They produce diagnostics, never corrected values.

The reviewer always sees the stored source reading (``raw_text``/display value).
"""

import re
import unicodedata
from typing import Optional

MATCH_KEY_VERSION = "mk1"

# Separators that commonly vary inside printed tax identifiers ("DE 123-456.789").
_TAX_ID_SEPARATORS = re.compile(r"[\s\-./_]+")
_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)
# Character confusions typical of OCR on invoices. Applied to *both* sides of a
# comparison, so they can only make an evidence diagnostic more tolerant.
_OCR_FOLD = str.maketrans({"o": "0", "i": "1", "l": "1", "|": "1", "s": "5",
                           "z": "2", "b": "8", "g": "9"})


def text_key(value) -> Optional[str]:
    """Unicode NFKC, casefold, collapse all whitespace. Punctuation is kept."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = " ".join(text.split())
    return text or None


def name_key(value) -> Optional[str]:
    """Exact party-name key. Kept conservative: punctuation differences stay distinct."""
    return text_key(value)


def tax_id_key(value) -> Optional[str]:
    """NFKC, uppercase, and remove whitespace and - . / _ separators."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).upper()
    text = _TAX_ID_SEPARATORS.sub("", text)
    return text or None


def party_match_key(name, tax_id) -> Optional[str]:
    """Identity key for Vendor/Customer nodes: exact name key AND exact tax-ID key.

    A party with no readable name gets no key (and therefore no node). Requiring both
    parts to agree can split one real company into two nodes (for example when one
    invoice omits the tax ID); that is deliberate. Splits are surfaced as review
    suggestions, whereas a wrong merge would silently mix two companies' facts.
    """
    name = name_key(name)
    if name is None:
        return None
    return f"{MATCH_KEY_VERSION}|name={name}|tax={tax_id_key(tax_id) or ''}"


def evidence_form(value) -> str:
    """Alphanumeric-only comparison form (drops whitespace, punctuation, wraps)."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return _NON_ALNUM.sub("", text)


def ocr_fold(form: str) -> str:
    """Fold common OCR confusions in an ``evidence_form`` string."""
    return form.translate(_OCR_FOLD)
