"""Deterministic product relevance classification for marketplace titles."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class RelevanceClass(StrEnum):
    EXACT_DEVICE = "exact_device"
    DEVICE_BUNDLE = "device_bundle"
    ACCESSORY = "accessory"
    PARTS_BROKEN = "parts_broken"
    WRONG_VARIANT = "wrong_variant"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class RelevanceDecision:
    classification: RelevanceClass
    is_relevant: bool
    reason: str

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "classification": self.classification.value,
            "is_relevant": self.is_relevant,
            "reason": self.reason,
        }


_BROKEN_PATTERNS = (
    r"\b(?:hs|casse|cassee|broken|defectueux|defective|endommage|endommagee)\b",
    r"\b(?:pour pieces?|for parts|a reparer|to repair|ne s allume plus|non fonctionnel)\b",
    r"\b(?:crashe|crashed|ecran fissure|sans garantie de fonctionnement)\b",
)

_ACCESSORY_PATTERNS = {
    "battery": r"\b(?:batterie|batteries|battery|batteries)\b",
    "case": r"\b(?:coque|housse|etui|case|boitier)\b",
    "charger": r"\b(?:chargeur|charger|charging case|boitier de charge)\b",
    "controller": r"\b(?:manette|manettes|controller|controllers|dualsense)\b",
    "dock": r"\b(?:dock|station d accueil|station de charge)\b",
    "attachment": r"\b(?:embout|embouts|nozzle|nozzles)\b",
    "game": r"\b(?:jeu|jeux|game|games|cartouche|cartouches)\b",
    "mount": r"\b(?:fixation|fixations|mount|mounts|support|supports|trepied|tripod)\b",
    "lens_cap": r"\b(?:bouchon|bouchons|lens cap|lens caps)\b",
    "strap": r"\b(?:bracelet|bracelets|strap|straps|band|bands)\b",
    "cable": r"\b(?:cable|cables|cordon|adaptateur|adapter)\b",
    "remote": r"\b(?:telecommande|remote)\b",
}

_DEVICE_NOUNS = re.compile(
    r"\b(?:console|camera|appareil|casque|ecouteurs?|telephone|smartphone|montre|drone|"
    r"imprimante|ordinateur|tablette|aspirateur|platine)\b"
)
_BUNDLE_MARKERS = re.compile(r"\+")
_ONLY_MARKERS = re.compile(r"\b(?:seul|seule|seulement|uniquement|only)\b")
_COMPATIBILITY_MARKERS = re.compile(r"\b(?:compatible|pour)\b")
_EMPTY_PACKAGING = re.compile(r"\b(?:boite|carton|emballage)(?: d origine)? vide\b")
_VARIANT_TERMS = {
    "digital",
    "digitale",
    "disc",
    "disque",
    "gen",
    "lcd",
    "lite",
    "max",
    "mark",
    "mk",
    "oled",
    "plus",
    "pro",
    "slim",
    "ti",
    "ultra",
}
_FAMILY_COMPETITORS = {
    "ps5": {"ps4", "xbox", "switch"},
    "xbox": {"ps4", "ps5", "switch"},
    "switch": {"ps4", "ps5", "xbox"},
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    ascii_text = re.sub(r"\bf\s*/?\s*(\d+)[.,](\d+)\b", r" f\1p\2 ", ascii_text)
    ascii_text = re.sub(r"\bf\s*/?\s*(\d+)\b", r" f\1 ", ascii_text)
    normalized = re.sub(r"[^a-z0-9+]+", " ", ascii_text)
    normalized = re.sub(r"\bwh(?=\d)", "wh ", normalized)
    normalized = re.sub(r"(\d)(mm|gb|tb)\b", r"\1 \2", normalized)
    normalized = re.sub(r"\bplay\s*station\s*([45])\b", r" ps\1 ", normalized)
    normalized = re.sub(r"\bplaystation\s*([45])\b", r" ps\1 ", normalized)
    return " ".join(normalized.split())


def _tokens(value: str) -> set[str]:
    return set(value.split())


def _identity_tokens(search_query: str) -> set[str]:
    stopwords = {"for", "the", "with", "pour", "avec"}
    return {
        token
        for token in _tokens(search_query)
        if token not in stopwords and (len(token) > 1 or token.isdigit())
    }


def _identifier_tokens(value: str) -> set[str]:
    return {token for token in _tokens(value) if any(character.isdigit() for character in token)}


def _target_discriminators(target: str, query: str) -> set[str]:
    query_tokens = _tokens(query)
    capacity_tokens = set(re.findall(r"\b\d+\s+(?:gb|tb)\b", target))
    capacity_values = {capacity.split()[0] for capacity in capacity_tokens}
    generation_values = set(
        re.findall(r"\b(?:gen|generation|mark|mk)\s+([0-9]+|i{1,3}|iv|v)\b", target)
    )
    aperture_values = {token for token in _tokens(target) if re.fullmatch(r"f\d+(?:p\d+)?", token)}
    return (
        (_VARIANT_TERMS & _tokens(target)) | capacity_values | generation_values | aperture_values
    ) - query_tokens


def _accessory_kinds(target: str, listing: str) -> set[str]:
    target_kinds = {
        kind for kind, pattern in _ACCESSORY_PATTERNS.items() if re.search(pattern, target)
    }
    return {
        kind
        for kind, pattern in _ACCESSORY_PATTERNS.items()
        if kind not in target_kinds and re.search(pattern, listing)
    }


def _starts_as_accessory(listing: str) -> bool:
    prefix = re.sub(r"^lot(?: de)?(?: \d+)? ", "", listing)
    return any(re.match(pattern, prefix) for pattern in _ACCESSORY_PATTERNS.values())


def _has_competing_family(target_tokens: set[str], listing_tokens: set[str]) -> bool:
    for family, competitors in _FAMILY_COMPETITORS.items():
        if family in target_tokens and competitors & listing_tokens:
            return True
    return False


def _decision(classification: RelevanceClass, reason: str) -> RelevanceDecision:
    return RelevanceDecision(
        classification=classification,
        is_relevant=classification in {RelevanceClass.EXACT_DEVICE, RelevanceClass.DEVICE_BUNDLE},
        reason=reason,
    )


def classify_listing_relevance(
    target_title: str,
    search_query: str,
    listing_title: str,
) -> RelevanceDecision:
    """Classify whether a listing title contains the requested working product.

    This deliberately favors precision: a title that does not establish the exact
    requested model is ``uncertain`` and is not accepted automatically.
    """
    target = _normalize(f"{target_title} {search_query}")
    query = _normalize(search_query or target_title)
    listing = _normalize(listing_title)
    if not listing or not query:
        return _decision(RelevanceClass.UNCERTAIN, "missing target or listing title")

    if any(re.search(pattern, listing) for pattern in _BROKEN_PATTERNS):
        return _decision(RelevanceClass.PARTS_BROKEN, "title indicates a broken or parts item")
    if _EMPTY_PACKAGING.search(listing):
        return _decision(RelevanceClass.ACCESSORY, "title offers empty product packaging")

    query_tokens = _identity_tokens(query)
    listing_tokens = _tokens(listing)
    base_identity_matches = bool(query_tokens) and query_tokens <= listing_tokens
    target_discriminators = _target_discriminators(target, query)
    identity_matches = base_identity_matches and target_discriminators <= listing_tokens
    accessory_kinds = _accessory_kinds(target, listing)

    if _has_competing_family(_tokens(target), listing_tokens):
        return _decision(
            RelevanceClass.UNCERTAIN, "title mixes the target with another device family"
        )
    if re.search(r"\blot de \d+ (?:consoles?|cameras?|appareils?)\b", listing):
        return _decision(RelevanceClass.UNCERTAIN, "title describes a multi-device lot")

    target_variants = _VARIANT_TERMS & _tokens(target)
    extra_variants = (_VARIANT_TERMS & listing_tokens) - target_variants
    if base_identity_matches and extra_variants:
        return _decision(
            RelevanceClass.WRONG_VARIANT,
            f"title adds another variant ({', '.join(sorted(extra_variants))})",
        )

    if (
        accessory_kinds
        and identity_matches
        and _DEVICE_NOUNS.search(listing)
        and _BUNDLE_MARKERS.search(listing)
    ):
        return _decision(RelevanceClass.DEVICE_BUNDLE, "target device is included with accessories")

    if accessory_kinds and (
        _starts_as_accessory(listing)
        or bool(_ONLY_MARKERS.search(listing))
        or not base_identity_matches
        or bool(_COMPATIBILITY_MARKERS.search(listing) and not _DEVICE_NOUNS.search(listing))
    ):
        return _decision(
            RelevanceClass.ACCESSORY,
            f"accessory-only title ({', '.join(sorted(accessory_kinds))})",
        )

    if base_identity_matches and not identity_matches:
        return _decision(
            RelevanceClass.WRONG_VARIANT,
            "title omits a model discriminator from the configured target",
        )

    if not identity_matches:
        target_identifiers = _identifier_tokens(query)
        listing_identifiers = _identifier_tokens(listing)
        family_tokens = {
            token for token in query_tokens if token not in target_identifiers and len(token) >= 3
        }
        if (
            target_identifiers
            and listing_identifiers
            and bool(target_identifiers - listing_identifiers)
            and bool(listing_identifiers - target_identifiers)
            and bool(family_tokens & listing_tokens)
        ):
            return _decision(RelevanceClass.WRONG_VARIANT, "title identifies a different model")
        return _decision(
            RelevanceClass.UNCERTAIN, "title does not establish the exact target model"
        )

    if accessory_kinds or _BUNDLE_MARKERS.search(listing):
        return _decision(RelevanceClass.DEVICE_BUNDLE, "target device is included with other items")
    return _decision(RelevanceClass.EXACT_DEVICE, "title matches the exact target model")
