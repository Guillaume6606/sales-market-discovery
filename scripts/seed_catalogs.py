"""Named product catalogs loaded explicitly with ``scripts/seed.py --catalog <name>``.

Price bounds are ESTIMATES from general knowledge, not measured market data. They are
deliberately wide: they only reject accessories, parts and implausible asks. They are
not resale valuations; the operator must validate them before activating a product.

``words_to_avoid`` is matched as a case-insensitive substring of the title, so every
entry is a phrase that cannot occur inside the exact reference's own title.

``required_tokens``/``excluded_tokens`` are the product identity used by title filtering
(whole normalized tokens, see ``ingestion.relevance.TOKEN_ALIASES``). Rule applied to
every reference: require the brand, both focal lengths, the mount brand of a third-party
lens, and only the markers that separate real variants (GM/G, II, G2, aperture...);
exclude neighbouring variants, other mounts and third-party brands.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class CatalogProduct:
    name: str
    category: str
    search_query: str
    brand: str
    price_min: int
    price_max: int
    words_to_avoid: tuple[str, ...]
    reference_tokens: tuple[str, ...] = field(default=())
    reference_excluded_tokens: tuple[str, ...] = field(default=())
    required_tokens: tuple[str, ...] = field(default=())
    excluded_tokens: tuple[str, ...] = field(default=())

    def description(self) -> str:
        return (
            f"Pilot catalog. Price bounds {self.price_min}-{self.price_max} EUR are unmeasured "
            "estimates to validate; they are not a resale value. Suggested valuation "
            f"reference tokens: required={', '.join(self.reference_tokens)}; "
            f"excluded={', '.join(self.reference_excluded_tokens) or 'none'}."
        )


LENS_CATEGORY = "Photo Lenses"

LENS_WORDS_TO_AVOID: tuple[str, ...] = (
    "pour pièces",
    "pour pieces",
    "hors service",
    "en panne",
    "défectueux",
    "defectueux",
    "cassé",
    "fissuré",
    "champignon",
    "moisissure",
    "rayé",
    "pare-soleil seul",
    "pare soleil seul",
    "bouchon seul",
    "bouchons seuls",
    "bague seule",
    "boîte seule",
    "boite seule",
    "boîte vide",
    "boite vide",
    "lot de",
    "lot ",
    "boîtier",
    "boitier",
    "réplique",
    "replica",
    "factice",
)

SONY_BODIES = ("a7", "a9", "a1 ", "fx3", "fx30", "zv-e", "a6")
CANON_BODIES = ("eos r", "r5", "r6", "r7", "r8", "r10", "r50", "rp ")
NIKON_BODIES = ("z5", "z6", "z7", "z8", "z9", "zf", "z30", "z50")
FUJI_BODIES = ("x-t", "x-h", "x-s", "x-pro", "x-e", "xt3", "xt4", "xt5", "xh2", "xs10", "xs20")
CANON_EF = ("canon ef", " ef ", "ef-", "adaptateur")
NIKON_F = ("af-s", "afs", "af s ", "af-p", "nikkor af", "ftz", "monture f")
OTHER_THAN_SONY_E = (
    "l-mount",
    "l mount",
    "monture l",
    "leica l",
    "lumix",
    "panasonic",
    "canon",
    "nikon",
    "fuji",
    "monture z",
    "monture x",
    "monture rf",
)
SONY_GM_II = ("gm ii", "gm2", "gm 2", "gmii", "mark ii", "mk ii", "mk2")


def _lens(
    name: str,
    search_query: str,
    brand: str,
    price_min: int,
    price_max: int,
    *exclusions: tuple[str, ...],
    reference_tokens: tuple[str, ...],
    reference_excluded_tokens: tuple[str, ...] = (),
) -> CatalogProduct:
    words = LENS_WORDS_TO_AVOID + tuple(word for group in exclusions for word in group)
    return CatalogProduct(
        name=name,
        category=LENS_CATEGORY,
        search_query=search_query,
        brand=brand,
        price_min=price_min,
        price_max=price_max,
        words_to_avoid=tuple(dict.fromkeys(words)),
        reference_tokens=reference_tokens,
        reference_excluded_tokens=reference_excluded_tokens,
    )


PILOT_LENSES: tuple[CatalogProduct, ...] = (
    _lens(
        "Sony FE 24-70mm f/2.8 GM II",
        "Sony 24-70 GM II",
        "Sony",
        900,
        2600,
        SONY_BODIES,
        reference_tokens=("sony", "24", "gm", "ii"),
    ),
    _lens(
        "Sony FE 24-70mm f/2.8 GM",
        "Sony 24-70 GM",
        "Sony",
        500,
        1800,
        SONY_BODIES,
        SONY_GM_II,
        reference_tokens=("sony", "24", "gm"),
        reference_excluded_tokens=("ii", "gm2"),
    ),
    _lens(
        "Sony FE 70-200mm f/2.8 GM OSS II",
        "Sony 70-200 GM II",
        "Sony",
        1100,
        3000,
        SONY_BODIES,
        reference_tokens=("sony", "70", "gm", "ii"),
    ),
    _lens(
        "Sony FE 70-200mm f/2.8 GM OSS",
        "Sony 70-200 GM",
        "Sony",
        700,
        2200,
        SONY_BODIES,
        SONY_GM_II,
        ("oss ii", "oss 2", "f/4", "f4 "),
        reference_tokens=("sony", "70", "gm"),
        reference_excluded_tokens=("ii", "gm2", "f4"),
    ),
    _lens(
        "Sony FE 70-200mm f/4 G OSS",
        "Sony 70-200 F4 G",
        "Sony",
        400,
        1400,
        SONY_BODIES,
        ("macro", "ii", "gm", "2.8", "2,8"),
        reference_tokens=("sony", "70", "g"),
        reference_excluded_tokens=("ii", "gm", "macro"),
    ),
    _lens(
        "Sony FE 16-35mm f/2.8 GM",
        "Sony 16-35 GM",
        "Sony",
        600,
        1900,
        SONY_BODIES,
        SONY_GM_II,
        ("pz", "f/4", "f4 "),
        reference_tokens=("sony", "16", "gm"),
        reference_excluded_tokens=("ii", "gm2"),
    ),
    _lens(
        "Sony FE 24-105mm f/4 G OSS",
        "Sony 24-105",
        "Sony",
        450,
        1300,
        SONY_BODIES,
        ("a-mount", "monture a", "sigma", "canon"),
        reference_tokens=("sony", "24", "g"),
    ),
    _lens(
        "Sony FE 135mm f/1.8 GM",
        "Sony 135 GM",
        "Sony",
        800,
        2100,
        SONY_BODIES,
        reference_tokens=("sony", "gm"),
    ),
    _lens(
        "Sony FE 85mm f/1.4 GM",
        "Sony 85 GM",
        "Sony",
        600,
        1800,
        SONY_BODIES,
        SONY_GM_II,
        ("1.8", "1,8"),
        reference_tokens=("sony", "gm"),
        reference_excluded_tokens=("ii", "gm2"),
    ),
    _lens(
        "Sony FE 100-400mm f/4.5-5.6 GM OSS",
        "Sony 100-400 GM",
        "Sony",
        1000,
        2700,
        SONY_BODIES,
        reference_tokens=("sony", "100", "gm"),
    ),
    _lens(
        "Canon RF 24-70mm f/2.8 L IS USM",
        "Canon RF 24-70",
        "Canon",
        1000,
        2600,
        CANON_BODIES,
        CANON_EF,
        reference_tokens=("canon", "rf", "24"),
    ),
    _lens(
        "Canon RF 70-200mm f/2.8 L IS USM",
        "Canon RF 70-200 F2.8",
        "Canon",
        1200,
        2900,
        CANON_BODIES,
        CANON_EF,
        ("usm z", "f/4", "f4"),
        reference_tokens=("canon", "rf", "70"),
    ),
    _lens(
        "Canon RF 70-200mm f/4 L IS USM",
        "Canon RF 70-200 F4",
        "Canon",
        700,
        1800,
        CANON_BODIES,
        CANON_EF,
        ("2.8", "2,8", "usm z"),
        reference_tokens=("canon", "rf", "70"),
    ),
    _lens(
        "Canon RF 15-35mm f/2.8 L IS USM",
        "Canon RF 15-35",
        "Canon",
        1000,
        2400,
        CANON_BODIES,
        CANON_EF,
        reference_tokens=("canon", "rf", "15"),
    ),
    _lens(
        "Canon RF 24-105mm f/4 L IS USM",
        "Canon RF 24-105 F4",
        "Canon",
        500,
        1300,
        CANON_BODIES,
        CANON_EF,
        ("stm", "7.1", "7,1", "2.8", "2,8"),
        reference_tokens=("canon", "rf", "24"),
        reference_excluded_tokens=("stm",),
    ),
    _lens(
        "Canon RF 100mm f/2.8 L Macro IS USM",
        "Canon RF 100 Macro",
        "Canon",
        700,
        1700,
        CANON_BODIES,
        CANON_EF,
        ("100-400", "100-500", "85mm"),
        reference_tokens=("canon", "rf", "macro"),
    ),
    _lens(
        "Canon RF 14-35mm f/4 L IS USM",
        "Canon RF 14-35",
        "Canon",
        800,
        1900,
        CANON_BODIES,
        CANON_EF,
        reference_tokens=("canon", "rf", "14"),
    ),
    _lens(
        "Nikon Z 24-70mm f/2.8 S",
        "Nikon Z 24-70 F2.8",
        "Nikon",
        1000,
        2500,
        NIKON_BODIES,
        NIKON_F,
        ("f/4", "f4", "ii"),
        reference_tokens=("z", "24"),
    ),
    _lens(
        "Nikon Z 70-200mm f/2.8 VR S",
        "Nikon Z 70-200",
        "Nikon",
        1300,
        2900,
        NIKON_BODIES,
        NIKON_F,
        ("ii", "f/4", "f4"),
        reference_tokens=("z", "70"),
        reference_excluded_tokens=("ii",),
    ),
    _lens(
        "Nikon Z 14-24mm f/2.8 S",
        "Nikon Z 14-24",
        "Nikon",
        1000,
        2500,
        NIKON_BODIES,
        NIKON_F,
        reference_tokens=("z", "14"),
    ),
    _lens(
        "Nikon Z 24-120mm f/4 S",
        "Nikon Z 24-120",
        "Nikon",
        500,
        1300,
        NIKON_BODIES,
        NIKON_F,
        reference_tokens=("z", "24"),
    ),
    _lens(
        "Nikon Z 100-400mm f/4.5-5.6 VR S",
        "Nikon Z 100-400",
        "Nikon",
        1300,
        3000,
        NIKON_BODIES,
        NIKON_F,
        reference_tokens=("z", "100"),
    ),
    _lens(
        "Nikon Z 50mm f/1.2 S",
        "Nikon Z 50 F1.2",
        "Nikon",
        1000,
        2400,
        NIKON_BODIES,
        NIKON_F,
        ("1.8", "1,8", "50-250"),
        reference_tokens=("z", "s"),
    ),
    _lens(
        "Fujifilm XF 16-55mm f/2.8 R LM WR",
        "XF 16-55",
        "Fujifilm",
        400,
        1200,
        FUJI_BODIES,
        ("ii", "wr2", "wr 2"),
        reference_tokens=("xf", "16", "wr"),
        reference_excluded_tokens=("ii",),
    ),
    _lens(
        "Fujifilm XF 16-55mm f/2.8 R LM WR II",
        "XF 16-55 II",
        "Fujifilm",
        750,
        1600,
        FUJI_BODIES,
        reference_tokens=("xf", "16", "ii"),
    ),
    _lens(
        "Fujifilm XF 50-140mm f/2.8 R LM OIS WR",
        "XF 50-140",
        "Fujifilm",
        650,
        1500,
        FUJI_BODIES,
        ("1.4x", "2x", "téléconvertisseur", "teleconverter"),
        reference_tokens=("xf", "50", "ois"),
    ),
    _lens(
        "Fujifilm XF 56mm f/1.2 R WR",
        "XF 56 WR",
        "Fujifilm",
        600,
        1300,
        FUJI_BODIES,
        ("apd",),
        reference_tokens=("xf", "wr"),
        reference_excluded_tokens=("apd",),
    ),
    _lens(
        "Fujifilm XF 8-16mm f/2.8 R LM WR",
        "XF 8-16",
        "Fujifilm",
        900,
        2000,
        FUJI_BODIES,
        reference_tokens=("xf", "8", "wr"),
    ),
    _lens(
        "Sigma 24-70mm f/2.8 DG DN Art (Sony E)",
        "Sigma 24-70 DG DN",
        "Sigma",
        500,
        1300,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        ("ii", "hsm"),
        reference_tokens=("sigma", "24", "dg", "dn", "art", "sony"),
        reference_excluded_tokens=("ii",),
    ),
    _lens(
        "Sigma 24-70mm f/2.8 DG DN II Art (Sony E)",
        "Sigma 24-70 DG DN II",
        "Sigma",
        800,
        1500,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        reference_tokens=("sigma", "24", "dg", "dn", "ii", "sony"),
    ),
    _lens(
        "Sigma 14-24mm f/2.8 DG DN Art (Sony E)",
        "Sigma 14-24 DG DN",
        "Sigma",
        650,
        1400,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        ("hsm",),
        reference_tokens=("sigma", "14", "dg", "dn", "sony"),
    ),
    _lens(
        "Sigma 85mm f/1.4 DG DN Art (Sony E)",
        "Sigma 85 DG DN",
        "Sigma",
        500,
        1100,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        ("hsm",),
        reference_tokens=("sigma", "dg", "dn", "sony"),
    ),
    _lens(
        "Tamron 28-75mm f/2.8 Di III VXD G2 (Sony E)",
        "Tamron 28-75 G2",
        "Tamron",
        400,
        950,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        ("rxd", "a036"),
        reference_tokens=("tamron", "28", "g2", "sony"),
        reference_excluded_tokens=("rxd",),
    ),
    _lens(
        "Tamron 70-180mm f/2.8 Di III VXD (Sony E)",
        "Tamron 70-180",
        "Tamron",
        450,
        1100,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        ("g2", "g 2"),
        reference_tokens=("tamron", "70", "vxd", "sony"),
        reference_excluded_tokens=("g2",),
    ),
    _lens(
        "Tamron 70-180mm f/2.8 Di III VXD G2 (Sony E)",
        "Tamron 70-180 G2",
        "Tamron",
        700,
        1400,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        reference_tokens=("tamron", "70", "g2", "sony"),
    ),
    _lens(
        "Tamron 35-150mm f/2-2.8 Di III VXD (Sony E)",
        "Tamron 35-150",
        "Tamron",
        900,
        2000,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        ("osd", "2.8-4"),
        reference_tokens=("tamron", "35", "vxd", "sony"),
    ),
    _lens(
        "Tamron 150-500mm f/5-6.7 Di III VC VXD (Sony E)",
        "Tamron 150-500",
        "Tamron",
        650,
        1400,
        SONY_BODIES,
        OTHER_THAN_SONY_E,
        reference_tokens=("tamron", "150", "vxd", "sony"),
    ),
)

LENS_EXCLUDED = ("teleconvertisseur", "teleconverter", "multiplicateur", "tc")
THIRD_PARTY = ("sigma", "tamron", "samyang", "viltrox", "laowa", "yongnuo", "ttartisan")
SONY_NATIVE = LENS_EXCLUDED + THIRD_PARTY
CANON_RF = LENS_EXCLUDED + THIRD_PARTY + ("ef", "efs")
NIKON_Z = LENS_EXCLUDED + THIRD_PARTY + ("af", "afs", "ftz")
FUJI_XF = LENS_EXCLUDED + THIRD_PARTY + ("xc",)
NOT_SONY_E = LENS_EXCLUDED + (
    "canon",
    "ef",
    "rf",
    "nikon",
    "z",
    "fujifilm",
    "x",
    "l",
    "leica",
    "panasonic",
    "lumix",
    "pentax",
)

IDENTITY_TOKENS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "Sony FE 24-70mm f/2.8 GM II": (("sony", "24", "70", "gm", "ii"), SONY_NATIVE + ("f4", "za")),
    "Sony FE 24-70mm f/2.8 GM": (
        ("sony", "24", "70", "gm"),
        SONY_NATIVE + ("ii", "mark", "mk", "f4", "za"),
    ),
    "Sony FE 70-200mm f/2.8 GM OSS II": (
        ("sony", "70", "200", "gm", "ii"),
        SONY_NATIVE + ("f4", "macro"),
    ),
    "Sony FE 70-200mm f/2.8 GM OSS": (
        ("sony", "70", "200", "gm"),
        SONY_NATIVE + ("ii", "mark", "mk", "f4", "macro"),
    ),
    "Sony FE 70-200mm f/4 G OSS": (
        ("sony", "70", "200", "g"),
        SONY_NATIVE + ("gm", "ii", "mark", "mk", "macro", "f2p8"),
    ),
    "Sony FE 16-35mm f/2.8 GM": (
        ("sony", "16", "35", "gm"),
        SONY_NATIVE + ("ii", "mark", "mk", "f4", "pz", "za"),
    ),
    "Sony FE 24-105mm f/4 G OSS": (("sony", "24", "105", "g"), SONY_NATIVE + ("ii",)),
    "Sony FE 135mm f/1.8 GM": (("sony", "135", "gm"), SONY_NATIVE + ("ii",)),
    "Sony FE 85mm f/1.4 GM": (
        ("sony", "85", "gm"),
        SONY_NATIVE + ("ii", "mark", "mk", "f1p8"),
    ),
    "Sony FE 100-400mm f/4.5-5.6 GM OSS": (("sony", "100", "400", "gm"), SONY_NATIVE + ("ii",)),
    "Canon RF 24-70mm f/2.8 L IS USM": (("canon", "rf", "24", "70"), CANON_RF + ("f4", "stm")),
    "Canon RF 70-200mm f/2.8 L IS USM": (("canon", "rf", "70", "200"), CANON_RF + ("f4", "z")),
    "Canon RF 70-200mm f/4 L IS USM": (
        ("canon", "rf", "70", "200", "f4"),
        CANON_RF + ("f2p8", "z"),
    ),
    "Canon RF 15-35mm f/2.8 L IS USM": (("canon", "rf", "15", "35"), CANON_RF),
    "Canon RF 24-105mm f/4 L IS USM": (
        ("canon", "rf", "24", "105"),
        CANON_RF + ("stm", "f2p8", "f7p1", "z"),
    ),
    "Canon RF 100mm f/2.8 L Macro IS USM": (
        ("canon", "rf", "100", "macro"),
        CANON_RF + ("85", "400", "500"),
    ),
    "Canon RF 14-35mm f/4 L IS USM": (("canon", "rf", "14", "35"), CANON_RF),
    "Nikon Z 24-70mm f/2.8 S": (("nikon", "z", "24", "70"), NIKON_Z + ("f4", "ii")),
    "Nikon Z 70-200mm f/2.8 VR S": (("nikon", "z", "70", "200"), NIKON_Z + ("f4", "ii")),
    "Nikon Z 14-24mm f/2.8 S": (("nikon", "z", "14", "24"), NIKON_Z),
    "Nikon Z 24-120mm f/4 S": (("nikon", "z", "24", "120"), NIKON_Z),
    "Nikon Z 100-400mm f/4.5-5.6 VR S": (("nikon", "z", "100", "400"), NIKON_Z),
    "Nikon Z 50mm f/1.2 S": (("nikon", "z", "50", "f1p2"), NIKON_Z + ("f1p8",)),
    "Fujifilm XF 16-55mm f/2.8 R LM WR": (("xf", "16", "55"), FUJI_XF + ("ii", "wr2")),
    "Fujifilm XF 16-55mm f/2.8 R LM WR II": (("xf", "16", "55", "ii"), FUJI_XF),
    "Fujifilm XF 50-140mm f/2.8 R LM OIS WR": (("xf", "50", "140"), FUJI_XF),
    "Fujifilm XF 56mm f/1.2 R WR": (("xf", "56", "wr"), FUJI_XF + ("apd",)),
    "Fujifilm XF 8-16mm f/2.8 R LM WR": (("xf", "8", "16"), FUJI_XF),
    "Sigma 24-70mm f/2.8 DG DN Art (Sony E)": (
        ("sigma", "24", "70", "sony"),
        NOT_SONY_E + ("ii", "hsm"),
    ),
    "Sigma 24-70mm f/2.8 DG DN II Art (Sony E)": (
        ("sigma", "24", "70", "ii", "sony"),
        NOT_SONY_E + ("hsm",),
    ),
    "Sigma 14-24mm f/2.8 DG DN Art (Sony E)": (
        ("sigma", "14", "24", "sony"),
        NOT_SONY_E + ("hsm",),
    ),
    "Sigma 85mm f/1.4 DG DN Art (Sony E)": (("sigma", "85", "sony"), NOT_SONY_E + ("hsm",)),
    "Tamron 28-75mm f/2.8 Di III VXD G2 (Sony E)": (
        ("tamron", "28", "75", "g2", "sony"),
        NOT_SONY_E + ("rxd", "a036"),
    ),
    "Tamron 70-180mm f/2.8 Di III VXD (Sony E)": (
        ("tamron", "70", "180", "sony"),
        NOT_SONY_E + ("g2",),
    ),
    "Tamron 70-180mm f/2.8 Di III VXD G2 (Sony E)": (
        ("tamron", "70", "180", "g2", "sony"),
        NOT_SONY_E,
    ),
    "Tamron 35-150mm f/2-2.8 Di III VXD (Sony E)": (
        ("tamron", "35", "150", "sony"),
        NOT_SONY_E + ("osd", "vc"),
    ),
    "Tamron 150-500mm f/5-6.7 Di III VC VXD (Sony E)": (
        ("tamron", "150", "500", "sony"),
        NOT_SONY_E,
    ),
}

PILOT_LENSES = tuple(
    replace(
        product,
        required_tokens=IDENTITY_TOKENS[product.name][0],
        excluded_tokens=IDENTITY_TOKENS[product.name][1],
    )
    for product in PILOT_LENSES
)

CATALOGS: dict[str, tuple[CatalogProduct, ...]] = {"pilot-lenses": PILOT_LENSES}
CATALOG_PROVIDERS: dict[str, list[str]] = {"pilot-lenses": ["leboncoin", "ebay"]}
