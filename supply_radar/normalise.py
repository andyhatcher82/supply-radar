"""Locale-aware normalisation.

Every match decision downstream rests on these functions, so they are kept
small, pure and directly tested. Nothing here calls a model: this is exactly
the layer where rules are knowable and an LLM would be the wrong tool.
"""

from __future__ import annotations

import re

import phonenumbers
import tldextract

from supply_radar.locales import LocalePack, _depunctuate, _fold

# Use the bundled public suffix snapshot rather than fetching it at runtime, so
# the pipeline is deterministic and works offline.
_extract = tldextract.TLDExtract(suffix_list_urls=())

_WHITESPACE = re.compile(r"\s+")


def fold_diacritics(text: str, locale: LocalePack) -> str:
    """Replace locale-specific accented characters with ASCII equivalents."""
    return _fold(text, locale.diacritics)


def strip_legal_suffix(name: str, locale: LocalePack) -> str:
    """Remove legal forms and ownership prefixes from a business name.

    Matching is on whole depunctuated tokens, so 'Doorway Tours' survives even
    though 'doo' is a legal form.
    """
    tokens = [t for t in _WHITESPACE.split(name.strip()) if t]

    changed = True
    while changed and tokens:
        changed = False

        head = _depunctuate(tokens[0]).lower()
        if head and (head in locale.name_prefixes or head in locale.legal_suffixes):
            tokens.pop(0)
            changed = True
            continue

        if tokens:
            tail = _depunctuate(tokens[-1]).lower()
            if tail and tail in locale.legal_suffixes:
                tokens.pop()
                changed = True

    return " ".join(tokens)


def _collapse_variants(token: str, locale: LocalePack) -> str:
    for variant, canonical in locale.transliteration_variants.items():
        token = token.replace(variant, canonical)
    return token


def normalise_name(name: str, locale: LocalePack) -> str:
    """Reduce a business name to a comparable identity key.

    Returns an empty string when a name consists only of generic descriptors.
    Callers must treat that as 'no name signal' rather than as a match, or
    every such record would collide with every other.
    """
    if not name:
        return ""

    folded = fold_diacritics(name, locale)
    stripped = strip_legal_suffix(folded, locale)

    tokens = []
    for raw_token in _WHITESPACE.split(stripped.lower()):
        token = _depunctuate(raw_token)
        if not token:
            continue
        token = _collapse_variants(token, locale)
        if token in locale.stopwords:
            continue
        tokens.append(token)

    return " ".join(tokens)


def normalise_phone(raw: str, region: str) -> str | None:
    """Return an E.164 phone string, or None when nothing parseable is present."""
    if not raw or not raw.strip():
        return None
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def registrable_domain(raw: str) -> str | None:
    """Reduce a URL or bare host to its registrable domain.

    'https://booking.adriatictours.hr/en' -> 'adriatictours.hr'. Multi-part
    suffixes such as co.uk are handled by the public suffix list rather than by
    counting dots.
    """
    if not raw or not raw.strip():
        return None
    result = _extract(raw.strip().lower())
    if not result.domain or not result.suffix:
        return None
    return f"{result.domain}.{result.suffix}"


# Website builders, where the registrable domain is the BUILDER and the operator
# is identified by the subdomain. Reducing to the registrable domain here is
# correct as normalisation and catastrophic as identity.
#
# Measured, not guessed: in the real Split sweep, EIGHT different operators
# collapsed onto wixsite.com, which then hard-matched them to each other.
BUILDER_DOMAINS = {
    "wixsite.com", "wix.com", "squarespace.com", "weebly.com",
    "wordpress.com", "blogspot.com", "godaddysites.com", "webnode.com",
    "webnode.hr", "jimdosite.com", "myshopify.com", "webflow.io",
    "netlify.app", "github.io", "carrd.co", "strikingly.com",
    "tilda.ws", "ucoz.com", "simplesite.com", "site123.me",
}

# Domains that are never the operator's own site, whatever the subdomain or
# path. Small operators frequently list a Facebook page or a marketplace
# listing as their "website", and matching two operators on facebook.com would
# be worse than having no domain signal at all.
NON_IDENTITY_DOMAINS = {
    "facebook.com", "instagram.com", "linktr.ee", "linkedin.com",
    "google.com", "business.site", "sites.google.com", "goo.gl",
    "tripadvisor.com", "tripadvisor.co.uk", "viator.com",
    "getyourguide.com", "airbnb.com", "booking.com", "klook.com",
    "whatsapp.com", "wa.me", "youtube.com", "tiktok.com",
    "civitatis.com", "expedia.com", "musement.com",
}


def identity_domain(raw: str) -> str | None:
    """The part of a URL that actually identifies the BUSINESS.

    Three cases:
      - a platform that is never the operator's own site  -> None
      - a website builder, where identity is the subdomain -> full host
      - anything else                                      -> registrable domain

    This exists because `registrable_domain` answers a different question. It
    answers "who owns this domain", which is the right normalisation and the
    wrong identity key the moment an operator is on a shared platform.
    """
    reg = registrable_domain(raw)
    if reg is None:
        return None
    if reg in NON_IDENTITY_DOMAINS:
        return None

    if reg in BUILDER_DOMAINS:
        result = _extract(raw.strip().lower())
        subdomain = (result.subdomain or "").removeprefix("www.")
        if not subdomain:
            # No subdomain to identify anyone by, so this carries no identity.
            return None
        return f"{subdomain}.{reg}"

    return reg
