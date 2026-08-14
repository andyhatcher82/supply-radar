"""Locale packs.

Normalisation rules are country-specific, not destination-specific. Croatia
strips d.o.o. and folds Č/Ć/Đ/Š/Ž; Italy needs S.r.l.; Spain needs S.L. Holding
these separately from the destination pack is what makes adding a country a
config change rather than a code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from supply_radar.config import CONFIG_DIR


@dataclass(frozen=True)
class LocalePack:
    locale: str
    language: str
    phone_region: str
    diacritics: dict[str, str] = field(default_factory=dict)
    transliteration_variants: dict[str, str] = field(default_factory=dict)
    legal_suffixes: tuple[str, ...] = ()
    name_prefixes: tuple[str, ...] = ()
    stopwords: frozenset[str] = frozenset()


def _depunctuate(token: str) -> str:
    """Reduce a token to comparable characters. 'd.o.o.' -> 'doo'."""
    return "".join(ch for ch in token if ch.isalnum())


def _fold(text: str, diacritics: dict[str, str]) -> str:
    return "".join(diacritics.get(ch, ch) for ch in text)


@lru_cache
def load_locale(code: str) -> LocalePack:
    path = CONFIG_DIR / "locales" / f"{code}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    diacritics = {str(k): str(v) for k, v in (raw.get("diacritics") or {}).items()}

    # Legal forms are compared in depunctuated form, longest first, so that
    # j.d.o.o. is consumed whole rather than leaving a stray "j".
    suffixes = tuple(
        sorted(
            {_depunctuate(s).lower() for s in (raw.get("legal_suffixes") or [])},
            key=len,
            reverse=True,
        )
    )
    prefixes = tuple(
        sorted(
            {_depunctuate(s).lower() for s in (raw.get("name_prefixes") or [])},
            key=len,
            reverse=True,
        )
    )

    # Stopwords are folded on load, because by the time they are applied the
    # name has already had its diacritics folded.
    stopwords = frozenset(
        _depunctuate(_fold(str(w).lower(), diacritics))
        for w in (raw.get("stopwords") or [])
    )

    return LocalePack(
        locale=raw["locale"],
        language=raw.get("language", raw["locale"]),
        phone_region=raw.get("phone_region", raw["locale"].upper()),
        diacritics=diacritics,
        transliteration_variants={
            str(k).lower(): str(v).lower()
            for k, v in (raw.get("transliteration_variants") or {}).items()
        },
        legal_suffixes=suffixes,
        name_prefixes=prefixes,
        stopwords=stopwords,
    )
