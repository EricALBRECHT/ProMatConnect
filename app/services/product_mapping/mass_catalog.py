"""Familles catalogue BRICO — identité extraite de la désignation, seed + apply EXACT.

Une famille = CategoryRule + IdentityModel. Le conditionnement (lot, boîte, m²
de carton) n'entre pas dans l'identité. La collection / le décor y entre
quand il distingue deux produits non équivalents.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, Supplier, SupplierProduct
from app.services.product_mapping import pipeline
from app.services.product_mapping.identity import PARTIAL_HIERARCHY, IdentityModel
from app.services.product_mapping.panneau_mdf_extractor import ExtractionResult
from app.services.product_mapping.primitives.textutil import fold, parse_number
from app.services.product_mapping.rules.base import CategoryRule, register_rule

REASON_NOT = "NOT_THIS_CATEGORY"

_PACK = re.compile(
    r"^(?:lot|pack|sachet|paquet|boite|set)\s+de\s+\d+\s+",
    re.I,
)
_QUOTE = re.compile(r'["«]([^"»]{2,40})["»]')
_CAPS = re.compile(r"\b([A-Z][A-Z0-9]{2,})\b")
_CAPS_BAN = {
    "LED", "PVC", "XPS", "MDF", "OSB", "PER", "USB", "NF", "DIN", "ISO",
    "AC4", "AC5", "AC6", "DIY", "BBC", "CEM", "CPJ",
}
_PAIR = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?\s*[x]\s*(?:[lhp]|ep|e)?\.?\s*"
    r"(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?"
    r"(?:\s*[x]\s*(?:ep(?:aisseur)?|e|ép)?\.?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?)?",
    re.I,
)
_LHP = re.compile(
    r"l\.?\s*(\d+(?:[.,]\d+)?)\s*(?:cm|mm)?\s*[x×]\s*"
    r"h\.?\s*(\d+(?:[.,]\d+)?)\s*(?:cm|mm)?\s*[x×]\s*"
    r"p\.?\s*(\d+(?:[.,]\d+)?)\s*(cm|mm)?",
    re.I,
)
_COLORS = (
    "blanc pur", "blanc casse", "extra blanc",
    "gris anthracite", "gris clair", "gris fonce", "gris acier", "gris perle",
    "beige clair", "beige fonce",
    "bleu ciel", "bleu marine", "bleu canard", "bleu gris",
    "vert sapin", "vert olive",
    "chene clair", "chene gris", "chene naturel", "chene blanchi", "chene fonce",
    "ivoire", "creme", "sable", "taupe", "anthracite", "ardoise", "graphite",
    "basalte", "ficelle", "terracotta", "bordeaux", "moutarde", "sauge",
    "greige", "pierre",
    "blanc", "noir", "gris", "beige", "bleu", "rouge", "vert", "marron",
    "orange", "jaune", "rose", "violet", "inox", "chrome", "dore", "cuivre",
    "naturel", "incolore", "wenge", "noyer", "hetre", "pin", "teck", "bronze",
    "laiton", "lin", "argile", "kaki", "perle",
)
_FINISH = (
    ("brillant", re.compile(r"\bbrillant\b")),
    ("velours", re.compile(r"\bvelours\b")),
    ("satin", re.compile(r"\bsatine?e?\b")),
    ("laque", re.compile(r"\blaque\b")),
    ("mat", re.compile(r"\bmate?\b")),
)

ParseFn = Callable[[str], dict[str, str] | None]


def strip_pack(text: str) -> str:
    return _PACK.sub("", text.strip())


def decor_token(text: str) -> str | None:
    quoted = _QUOTE.search(text)
    if quoted:
        token = fold(quoted.group(1)).strip(" -")
        return token or None
    caps = [c for c in _CAPS.findall(text) if c not in _CAPS_BAN and not c.isdigit()]
    if caps:
        return fold(caps[0])
    return None


def _mm(raw: str, unit: str | None, *, bare: str) -> int | None:
    number = parse_number(raw)
    if number is None:
        return None
    unit = (unit or "").lower()
    if unit == "mm":
        value = float(number)
    elif unit == "cm":
        value = float(number) * 10
    elif unit == "m":
        value = float(number) * 1000
    elif bare == "cm":
        value = float(number) * 10
    elif bare == "mm":
        value = float(number)
    else:
        return None
    rounded = int(round(value))
    if rounded <= 0 or rounded > 8000:
        return None
    return rounded


def _norm(text: str) -> str:
    cleaned = (
        text.replace("×", "x")
        .replace("✕", "x")
        .replace("⌀", "ø")
        .replace("Ø", "ø")
        .replace("∅", "ø")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
    )
    return fold(cleaned)


def format_pair(text: str, *, bare: str = "cm", loose_thickness: bool = False) -> str | None:
    """Format canonique « LxW » ou « LxWxE » en millimètres, côtés triés."""
    normalized = _norm(text)
    matches = list(_PAIR.finditer(normalized))
    if not matches:
        return None
    match = next((m for m in reversed(matches) if any(m.group(i) for i in (2, 4, 6))), matches[-1])
    unit = match.group(2) or match.group(4) or match.group(6)
    length = _mm(match.group(1), unit, bare=bare)
    width = _mm(match.group(3), match.group(4) or unit, bare=bare)
    if length is None or width is None:
        return None
    sides = sorted((length, width), reverse=True)
    thick_raw = match.group(5)
    thick = None
    if thick_raw:
        thick = _mm(thick_raw, match.group(6), bare="mm" if (match.group(6) or "").lower() != "cm" else "cm")
    if thick is None and loose_thickness:
        lone = re.search(r"\b(\d+(?:[.,]\d+)?)\s*mm\b", normalized)
        if lone:
            thick = _mm(lone.group(1), "mm", bare="mm")
            if thick is not None and not 4 <= thick <= 30:
                thick = None
    if thick:
        return f"{sides[0]}x{sides[1]}x{thick}"
    return f"{sides[0]}x{sides[1]}"


def format_lhp(text: str) -> str | None:
    match = _LHP.search(_norm(text))
    if not match:
        generic = format_pair(text)
        return generic
    unit = match.group(4) or "cm"
    dims = []
    for index in (1, 2, 3):
        value = _mm(match.group(index), unit if index == 3 else unit, bare="cm")
        if value is None:
            return None
        dims.append(value)
    return f"L{dims[0]}xH{dims[1]}xP{dims[2]}"


def color_of(blob: str) -> str | None:
    blob = blob.replace("blanche", "blanc").replace("blancs", "blanc")
    for phrase in _COLORS:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", blob):
            return phrase.replace(" ", "_")
    return None


def finish_of(blob: str) -> str | None:
    for name, pattern in _FINISH:
        if pattern.search(blob):
            return name
    return None


def volume_token(blob: str) -> str | None:
    ml = re.search(r"(\d+(?:[.,]\d+)?)\s*ml\b", blob)
    if ml:
        number = parse_number(ml.group(1))
        if number is not None:
            return f"{format(number.normalize(), 'f')}ml"
    liters = re.search(r"(\d+(?:[.,]\d+)?)\s*l(?:itres?)?\b", blob)
    if liters:
        number = parse_number(liters.group(1))
        if number is not None and number <= 30:
            return f"{format(number.normalize(), 'f')}l"
    return None


def weight_token(blob: str) -> str | None:
    grams = re.search(r"(\d+(?:[.,]\d+)?)\s*g\b", blob)
    kilos = re.search(r"(\d+(?:[.,]\d+)?)\s*kg\b", blob)
    if kilos:
        number = parse_number(kilos.group(1))
        if number is not None and number <= 40:
            return f"{format(number.normalize(), 'f')}kg"
    if grams and not kilos:
        number = parse_number(grams.group(1))
        if number is not None and 50 <= number <= 5000:
            return f"{format(number.normalize(), 'f')}g"
    return None


def power_token(blob: str) -> str | None:
    match = re.search(r"(\d{2,5})\s*w\b", blob)
    if not match:
        return None
    watts = int(match.group(1))
    if watts < 20 or watts > 5000:
        return None
    return str(watts)


def diameter_mm(blob: str) -> str | None:
    blob = _norm(blob)
    match = re.search(r"(?:ø|diam(?:etre)?\.?)\s*:?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm)?", blob)
    if not match:
        match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*mm\b", blob)
        if not match:
            return None
        value = _mm(match.group(1), "mm", bare="mm")
    else:
        value = _mm(match.group(1), match.group(2), bare="mm")
    if value is None or value > 400:
        return None
    return str(value)


def _blocked(blob: str) -> bool:
    return any(
        token in blob
        for token in (
            "h07vu",
            "h05vvf",
            " r2v",
            "beton cellulaire",
            "siporex",
            "ytong",
            "polystyrene extrud",
            "granul",
        )
    )


def parse_liquide(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if any(word in blob for word in ("rouleau", "pinceau", "pistolet", "bac ", "ruban", "decapant", "diluant", "decaps", "ouvre", "manchon", "pack complet")):
        return None
    if "lasure" in blob:
        kind = "lasure"
    elif "vitrificateur" in blob:
        kind = "vitrificateur"
    elif "saturateur" in blob:
        kind = "saturateur"
    elif "vernis" in blob:
        kind = "vernis"
    elif "sous couche" in blob or "sous-couche" in blob:
        kind = "sous_couche"
    elif "peinture" in blob:
        kind = "peinture"
    else:
        return None
    if "facade" in blob:
        usage = "facade"
    elif "sol" in blob or "escalier" in blob:
        usage = "sol"
    elif "boiserie" in blob:
        usage = "boiserie"
    elif "metal" in blob or "fer " in blob:
        usage = "metal"
    elif "interieur" in blob and "exterieur" in blob:
        usage = "interieur_exterieur"
    elif "mur" in blob and "plafond" in blob:
        usage = "mur_plafond"
    elif "mur" in blob:
        usage = "mur"
    elif "plafond" in blob:
        usage = "plafond"
    elif "exterieur" in blob:
        usage = "exterieur"
    elif kind != "peinture":
        usage = "bois"
    else:
        usage = ""
    finish = finish_of(blob) or ("aerosol" if "aerosol" in blob or "spray" in blob else "")
    color = color_of(blob) or ("pierre" if "ton pierre" in blob else "")
    volume = volume_token(blob) or ""
    gamme = decor_token(text) or "std"
    return {"kind": kind, "usage": usage, "finish": finish, "color": color, "volume": volume, "gamme": gamme}


def parse_carrelage(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "plinthe" in blob or not any(word in blob for word in ("carrelage", "faience")):
        return None
    if any(word in blob for word in ("lunette", "coupe carrelage", "bouchon", "croisillon", "spatule")):
        return None
    if "exterieur" in blob and "sol" in blob:
        usage = "sol_ext"
    elif "exterieur" in blob:
        usage = "exterieur"
    elif "faience" in blob or "mural" in blob:
        usage = "mur"
    elif "sol" in blob:
        usage = "sol"
    else:
        usage = ""
    if "gres" in blob:
        material = "gres"
    elif "faience" in blob:
        material = "faience"
    else:
        material = "carrelage"
    decor = decor_token(text) or ""
    aspect = ""
    for name in ("pierre", "bois", "beton", "marbre", "ciment", "metal", "uni"):
        if f"aspect {name}" in blob or f"effet {name}" in blob:
            aspect = name
            break
    if not decor:
        decor = aspect
    return {
        "usage": usage,
        "material": material,
        "decor": decor,
        "color": color_of(blob) or "",
        "finish": finish_of(blob) or "std",
        "format": format_pair(text) or "",
    }


def parse_stratifie(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "stratif" not in blob:
        return None
    if any(word in blob for word in ("kit de pose", "pare vapeur", "joint", "plinthe", "sous couche", "film ", "plan de travail", "plan travail")):
        return None
    decor = decor_token(text) or ""
    if not decor:
        for name in ("chene clair", "chene gris", "chene", "bois clair", "noyer", "pin", "beton", "hetre", "beige"):
            if name in blob:
                decor = name.replace(" ", "_")
                break
    return {"decor": decor, "format": format_pair(text, loose_thickness=True) or "", "usage": "sol"}


def parse_lame(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "lame" not in blob or any(word in blob for word in ("scie", "taille", "stratif", "rideau")):
        return None
    if "terrasse" in blob:
        kind = "terrasse"
    elif "pvc" in blob or "vinyle" in blob:
        kind = "pvc"
    elif "bardage" in blob or "clin" in blob:
        kind = "bardage"
    elif "sol" in blob:
        kind = "sol"
    else:
        return None
    if "composite" in blob:
        material = "composite"
    elif "pin" in blob:
        material = "pin"
    elif "bois" in blob or "chene" in blob:
        material = "bois"
    elif "pvc" in blob:
        material = "pvc"
    else:
        material = ""
    return {
        "kind": kind,
        "material": material,
        "color": color_of(blob) or "nr",
        "format": format_pair(text) or "",
    }


def parse_plinthe(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "plinthe" not in blob:
        return None
    decor = decor_token(text) or ""
    if not decor:
        for name in ("chene clair", "chene", "blanc", "gris", "noir", "pierre", "beton"):
            if name in blob:
                decor = name.replace(" ", "_")
                break
    material = "gres" if "gres" in blob else "mdf" if "mdf" in blob else "bois" if "bois" in blob or "chene" in blob else "std"
    return {"decor": decor, "material": material, "format": format_pair(text) or ""}


def parse_meuble(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "facade" in blob or "douche" in blob:
        return None
    if not (
        blob.startswith("meuble")
        or "meuble " in blob[:24]
        or blob.startswith("colonne")
    ):
        return None
    if blob.startswith("kit") or blob.startswith("ensemble"):
        return None
    if "sous vasque" in blob or "sous-vasque" in blob:
        kind = "sous_vasque"
    elif "vasque" in blob:
        kind = "vasque"
    elif blob.startswith("colonne") or "colonne" in blob:
        kind = "colonne"
    elif "haut" in blob:
        kind = "haut"
    elif "bas" in blob:
        kind = "bas"
    elif "angle" in blob:
        kind = "angle"
    elif "armoire" in blob:
        kind = "armoire"
    else:
        kind = "meuble"
    return {
        "kind": kind,
        "collection": decor_token(text) or "",
        "color": color_of(blob) or "",
        "finish": finish_of(blob) or "nr",
        "format": format_lhp(text) or "",
    }


def parse_facade(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "facade" not in blob or any(word in blob for word in ("peinture", "manchon", "traitement", "enduit", "rouleau")):
        return None
    return {
        "kind": "facade",
        "collection": decor_token(text) or "",
        "color": color_of(blob) or "",
        "finish": finish_of(blob) or "nr",
        "format": format_lhp(text) or format_pair(text) or "",
    }


def parse_bloc_porte(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "bloc porte" not in blob and "bloc-porte" not in blob and "bloque porte" not in blob:
        return None
    if "cale" in blob:
        return None
    width = re.search(r"(\d{2,3})\s*cm\b", blob)
    if "gauche" in blob:
        sens = "gauche"
    elif "droit" in blob:
        sens = "droit"
    else:
        sens = ""
    if "postform" in blob:
        kind = "postforme"
    elif "isoplane" in blob:
        kind = "isoplane"
    elif "vitre" in blob or "vitree" in blob:
        kind = "vitre"
    elif "isolant" in blob:
        kind = "isolant"
    else:
        kind = "bloc"
    return {
        "kind": kind,
        "width_cm": width.group(1) if width else "",
        "sens": sens,
        "color": color_of(blob) or "nr",
    }


def parse_fenetre(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if not blob.startswith("fenetre"):
        return None
    if any(word in blob for word in ("store", "poignee", "raccord", "volet", "moustiquaire", "joint", "film")):
        return None
    if "pvc" in blob:
        material = "pvc"
    elif "aluminium" in blob or " alu" in blob:
        material = "alu"
    elif "bois" in blob:
        material = "bois"
    else:
        material = ""
    if "oscillo" in blob:
        opening = "oscillo_battant"
    elif "coulissant" in blob:
        opening = "coulissant"
    elif "battant" in blob:
        opening = "battant"
    else:
        opening = "nr"
    if "toit" in blob and not material:
        material = "toit"
    return {"material": material, "opening": opening, "format": format_pair(text) or "", "color": color_of(blob) or "nr"}


def parse_store(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "store" not in blob and "volet" not in blob:
        return None
    kind = "volet" if "volet" in blob else "store"
    return {"kind": kind, "format": format_pair(text) or "", "color": color_of(blob) or ""}


def parse_radiateur(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if not blob.startswith("radiateur"):
        return None
    if "seche" in blob and "serviette" in blob:
        kind = "seche_serviette"
    elif "ceramique" in blob:
        kind = "ceramique"
    elif "inertie" in blob:
        kind = "inertie"
    elif "fonte" in blob:
        kind = "fonte"
    elif "eau" in blob or "hydraulique" in blob:
        kind = "eau"
    elif "electrique" in blob:
        kind = "electrique"
    else:
        kind = ""
    fmt = format_pair(text) or ""
    modele = decor_token(text) or ("std" if fmt else "")
    return {
        "kind": kind,
        "power_w": power_token(blob) or "",
        "color": color_of(blob) or "",
        "modele": modele,
        "format": fmt or ("nr" if modele else ""),
    }


def parse_tube(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if not blob.startswith("tube"):
        return None
    if any(word in blob for word in ("colle", "mastic", "led", "neon", "fluo", "cuivre esthetique")):
        return None
    if "multicouche" in blob:
        material = "multicouche"
    elif "cuivre" in blob:
        material = "cuivre"
    elif "per" in blob:
        material = "per"
    elif "pvc" in blob:
        material = "pvc"
    elif "acier" in blob:
        material = "acier"
    else:
        material = ""
    length = re.search(r"(\d+(?:[.,]\d+)?)\s*m\b(?!m|l)", blob)
    length_s = ""
    if length:
        number = parse_number(length.group(1))
        if number is not None and number <= 50:
            length_s = f"{format(number.normalize(), 'f')}m"
    return {"material": material, "diameter_mm": diameter_mm(blob) or "", "length": length_s}


def parse_raccord(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if not any(blob.startswith(word) for word in ("raccord", "coude", "manchon", "mamelon", "collecteur", "reduction", "union", "bouchon", "te ")):
        return None
    if "fenetre" in blob or "toiture" in blob:
        return None
    forms = (
        ("collecteur", "collecteur"),
        ("coude", "coude"),
        ("manchon", "manchon"),
        ("mamelon", "mamelon"),
        ("reduction", "reduction"),
        ("union", "union"),
        ("bouchon", "bouchon"),
        ("te", "te"),
        ("raccord", "raccord"),
    )
    kind = next(name for token, name in forms if token in blob)
    if "multicouche" in blob:
        material = "multicouche"
    elif "cuivre" in blob or "bicone" in blob or "laiton" in blob:
        material = "cuivre"
    elif re.search(r"\bper\b", blob):
        material = "per"
    elif "pvc" in blob:
        material = "pvc"
    else:
        material = ""
    if "sertir" in blob:
        system = "sertir"
    elif "souder" in blob:
        system = "souder"
    elif "bicone" in blob or "bague" in blob:
        system = "bicone"
    elif "glissement" in blob:
        system = "glissement"
    elif "visser" in blob:
        system = "visser"
    else:
        system = "nr"
    if not material and system == "sertir":
        material = "multicouche"
    elif not material and system in ("souder", "bicone"):
        material = "cuivre"
    elif not material and system == "glissement":
        material = "per"
    thread = re.search(r"(\d{2})\s*/\s*(\d{2})", blob)
    thread_s = f"{thread.group(1)}_{thread.group(2)}" if thread else "nr"
    diameter = diameter_mm(blob) or (thread_s if thread_s != "nr" else "")
    return {"kind": kind, "material": material, "diameter_mm": diameter, "thread": thread_s, "system": system}


def parse_mitigeur(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "mitigeur" not in blob and "melangeur" not in blob:
        return None
    if any(word in blob for word in ("cartouche", "joint", "flexible pour")):
        return None
    if "douche" in blob:
        usage = "douche"
    elif "evier" in blob or "cuisine" in blob:
        usage = "evier"
    elif "bain" in blob:
        usage = "bain"
    elif "bidet" in blob:
        usage = "bidet"
    elif "lavabo" in blob:
        usage = "lavabo"
    else:
        usage = ""
    if "dore" in blob or "effet or" in blob:
        finish = "dore"
    elif "noir" in blob:
        finish = "noir"
    elif "blanc" in blob:
        finish = "blanc"
    elif "brosse" in blob:
        finish = "brosse"
    elif "chrome" in blob or "chrom" in blob:
        finish = "chrome"
    else:
        finish = ""
    return {"usage": usage, "finish": finish, "modele": decor_token(text) or ""}


def parse_appareillage(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    gammes = ("odace", "dooxie", "celiane", "niloe", "mosaic", "plexo", "arnould", "zephyr")
    gamme = next((name for name in gammes if name in blob), "")
    if not gamme:
        return None
    if "va et vient" in blob or "va-et-vient" in blob:
        kind = "va_et_vient"
    elif "interrupteur" in blob:
        kind = "interrupteur"
    elif "prise" in blob:
        kind = "prise"
    elif "poussoir" in blob:
        kind = "poussoir"
    elif "rj45" in blob:
        kind = "rj45"
    else:
        kind = ""
    pose = "saillie" if "saillie" in blob else "encastre" if "encastre" in blob else "nr"
    return {"gamme": gamme, "kind": kind, "pose": pose, "color": color_of(blob) or ""}


def parse_enduit(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "enduit" not in blob:
        return None
    if "rebouchage" in blob:
        fonction = "rebouchage"
    elif "lissage" in blob:
        fonction = "lissage"
    elif "joint" in blob:
        fonction = "joint"
    elif "facade" in blob:
        fonction = "facade"
    elif "garnissant" in blob or "degross" in blob:
        fonction = "garnissant"
    elif "multi" in blob:
        fonction = "multi"
    else:
        fonction = ""
    form = "pate" if "pate" in blob else "poudre" if "poudre" in blob else "nr"
    return {"fonction": fonction, "form": form, "poids": weight_token(blob) or ""}


def parse_mortier(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "mortier" not in blob:
        return None
    if "colle" in blob or "flex" in blob:
        kind = "colle"
    elif "impermeab" in blob:
        kind = "impermeabilisation"
    elif "refractaire" in blob:
        kind = "refractaire"
    elif "rapide" in blob:
        kind = "rapide"
    elif "universel" in blob:
        kind = "universel"
    elif "joint" in blob:
        kind = "joint"
    else:
        kind = ""
    return {"kind": kind, "poids": weight_token(blob) or ""}


def parse_colle(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "colle" not in blob or "mortier" in blob or "enduit" in blob:
        return None
    if "pistolet" in blob and "colle" not in blob.split("pistolet")[0]:
        return None
    kinds = (
        ("neoprene", "neoprene"),
        ("polyurethane", "polyurethane"),
        ("bitume", "bitume"),
        ("cyano", "cyano"),
        ("carrelage", "carrelage"),
        ("plinthe", "plinthe"),
        ("parquet", "parquet"),
        ("moquette", "moquette"),
        ("polystyr", "polystyrene"),
        ("pvc", "pvc"),
        ("bois", "bois"),
        ("multi", "multi"),
    )
    kind = next((name for token, name in kinds if token in blob), "")
    qty = weight_token(blob) or volume_token(blob) or ""
    return {"kind": kind, "quantite": qty}


def parse_laine(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "laine de verre" in blob or ("laine" in blob and "verre" in blob):
        material = "verre"
    elif "laine de roche" in blob or ("laine" in blob and "roche" in blob):
        material = "roche"
    elif "ouate" in blob:
        material = "ouate"
    else:
        return None
    thick = re.search(r"(\d+(?:[.,]\d+)?)\s*mm\b", blob)
    thickness = ""
    if thick:
        value = _mm(thick.group(1), "mm", bare="mm")
        if value and 20 <= value <= 400:
            thickness = str(value)
    r_value = re.search(r"\br\s*[:=]?\s*(\d+(?:[.,]\d+)?)", blob)
    r_token = ""
    if r_value:
        number = parse_number(r_value.group(1))
        if number is not None and number <= 15:
            r_token = format(number.normalize(), "f")
    form = "rouleau" if "rouleau" in blob else "panneau" if "panneau" in blob else ""
    return {"material": material, "thickness_mm": thickness, "form": form, "r_value": r_token or "nr", "format": format_pair(text) or "nr"}


def parse_joint(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if not blob.startswith("joint"):
        return None
    if "enduit" in blob:
        return None
    if "silicone" in blob:
        kind = "silicone"
    elif "carrelage" in blob or "gres" in blob:
        kind = "carrelage"
    elif "acryl" in blob:
        kind = "acrylique"
    else:
        kind = ""
    return {"kind": kind, "color": color_of(blob) or "", "quantite": weight_token(blob) or volume_token(blob) or ""}


def parse_sanitaire(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if blob.startswith("kit") or blob.startswith("ensemble") or "meuble" in blob:
        return None
    if "receveur" in blob:
        kind = "receveur"
    elif "baignoire" in blob:
        kind = "baignoire"
    elif re.search(r"\bwc\b|cuvette", blob):
        kind = "wc"
    elif "vasque" in blob and "meuble" not in blob:
        kind = "vasque"
    elif "lave mains" in blob or "lave-mains" in blob:
        kind = "lave_mains"
    elif "colonne de douche" in blob:
        kind = "colonne_douche"
    else:
        return None
    modele = decor_token(text) or ""
    fmt = format_pair(text) or diameter_mm(blob) or ""
    color = color_of(blob) or ""
    if modele and not fmt:
        fmt = "nr"
    if modele and not color:
        color = "nr"
    if not modele and fmt and color:
        modele = "std"
    return {"kind": kind, "modele": modele, "color": color, "format": fmt}


def parse_poignee(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if not blob.startswith("poignee"):
        return None
    if "fenetre" in blob:
        kind = "fenetre"
    elif "meuble" in blob or "porte de meuble" in blob:
        kind = "meuble"
    elif "porte" in blob:
        kind = "porte"
    else:
        kind = ""
    entraxe = re.search(r"entraxe\s*(\d+)", blob)
    if "dore" in blob or "effet or" in blob:
        finish = "dore"
    elif "inox" in blob:
        finish = "inox"
    elif "noir" in blob:
        finish = "noir"
    elif "blanc" in blob:
        finish = "blanc"
    elif "chrome" in blob or "chrom" in blob:
        finish = "chrome"
    elif "laiton" in blob:
        finish = "laiton"
    else:
        finish = ""
    return {
        "kind": kind,
        "finish": finish,
        "entraxe": entraxe.group(1) if entraxe else "nr",
        "modele": decor_token(text) or "",
    }


def parse_serrure(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if blob.startswith("cadenas") or " cadenas" in blob[:20]:
        size = re.search(r"(\d{2,3})\s*mm\b", blob)
        return {"kind": "cadenas", "axe": size.group(1) if size else "", "sens": "nr"}
    if not blob.startswith("serrure"):
        return None
    if "5 point" in blob:
        kind = "5_points"
    elif "3 point" in blob:
        kind = "3_points"
    elif "cylindre" in blob:
        kind = "cylindre"
    else:
        kind = "serrure"
    if "gauche" in blob:
        sens = "gauche"
    elif "droit" in blob:
        sens = "droit"
    else:
        sens = ""
    axe = re.search(r"(\d{2})\s*[x×]\s*(\d{2})", blob)
    return {"kind": kind, "axe": f"{axe.group(1)}x{axe.group(2)}" if axe else "", "sens": sens}


def parse_foret(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "foret" not in blob:
        return None
    if any(word in blob for word in ("perceuse", "visseuse", "porte foret", "porte-foret", "kit")):
        return None
    if "beton" in blob:
        material = "beton"
    elif "bois" in blob:
        material = "bois"
    elif "metal" in blob or "metaux" in blob:
        material = "metal"
    elif "universel" in blob or "multi" in blob:
        material = "multi"
    else:
        material = ""
    pair = format_pair(text, bare="mm")
    return {"material": material, "format": pair or ""}


def parse_bois(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    kinds = ("tasseau", "chevron", "lambourde", "madrier", "bastaing", "volige", "latte")
    kind = next((name for name in kinds if name in blob), "")
    if not kind:
        return None
    if "chene" in blob:
        essence = "chene"
    elif "douglas" in blob:
        essence = "douglas"
    elif "pin" in blob:
        essence = "pin"
    elif "sapin" in blob or "epicea" in blob:
        essence = "sapin"
    else:
        essence = ""
    if "autoclave" in blob or "classe 4" in blob:
        essence = f"{essence}_autoclave" if essence else "autoclave"
    length = re.search(r"(\d+(?:[.,]\d+)?)\s*m\b(?!m)", blob)
    length_s = ""
    if length:
        number = parse_number(length.group(1))
        if number is not None and number <= 8:
            length_s = f"{format(number.normalize(), 'f')}m"
    return {"kind": kind, "essence": essence, "section": format_pair(text) or "", "length": length_s}


def parse_plan(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "plan de travail" not in blob and not blob.startswith("plan travail"):
        return None
    if "stratif" in blob:
        material = "stratifie"
    elif "bois" in blob or "chene" in blob:
        material = "bois"
    elif "resine" in blob:
        material = "resine"
    else:
        material = ""
    return {
        "material": material,
        "decor": decor_token(text) or color_of(blob) or "",
        "format": format_pair(text) or "",
    }


def parse_kit(designation: str) -> dict[str, str] | None:
    text = designation.strip()
    blob = fold(text)
    if not (blob.startswith("kit") or blob.startswith("ensemble")):
        return None
    nouns = (
        "douche", "meuble", "porte", "wc", "salle", "fixation", "plomberie",
        "robinet", "coulissant", "cuisine", "bain", "sol", "terrasse",
    )
    kind = next((name for name in nouns if name in blob), "")
    return {
        "kind": kind,
        "modele": decor_token(text) or "",
        "format": format_lhp(text) or format_pair(text) or "nr",
        "color": color_of(blob) or "nr",
    }


def parse_luminaire(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if blob.startswith("spot") or " spot " in blob[:20]:
        kind = "spot"
    elif blob.startswith("applique"):
        kind = "applique"
    elif blob.startswith("plafonnier"):
        kind = "plafonnier"
    elif blob.startswith("reglette") or blob.startswith("réglette"):
        kind = "reglette"
    else:
        return None
    power = power_token(blob) or ""
    fmt = format_pair(text) or ""
    modele = decor_token(text) or ""
    if not power and not fmt and not modele:
        power = ""
    return {
        "kind": kind,
        "power_w": power or ("nr" if fmt or modele else ""),
        "color": color_of(blob) or "",
        "format": fmt or ("nr" if power or modele else ""),
        "modele": modele or ("std" if power or fmt else ""),
    }


def parse_porte_rang(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if blob.startswith("porte coulissante"):
        kind = "coulissante"
    elif "porte de placard" in blob or blob.startswith("porte placard"):
        kind = "placard"
    else:
        return None
    color = color_of(blob) or ""
    if not color and "chene" in blob:
        color = "chene"
    elif not color and "miel" in blob:
        color = "miel"
    return {
        "kind": kind,
        "collection": decor_token(text) or "",
        "color": color,
        "format": format_pair(text) or "",
    }


def parse_equerre(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if "equerre" not in blob:
        return None
    if "charge" in blob:
        kind = "charge"
    elif "etagere" in blob or "tablette" in blob:
        kind = "etagere"
    elif "bardage" in blob:
        kind = "bardage"
    elif "fenetre" in blob or "volet" in blob:
        kind = "menuiserie"
    else:
        kind = ""
    return {"kind": kind, "format": format_pair(text) or ""}


def parse_disque(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if not blob.startswith("disque"):
        return None
    if "diamant" in blob:
        material = "diamant"
    elif "beton" in blob:
        material = "beton"
    elif "bois" in blob:
        material = "bois"
    elif "metal" in blob or "acier" in blob or "inox" in blob:
        material = "metal"
    else:
        material = ""
    return {"material": material, "diameter_mm": diameter_mm(blob) or ""}


def parse_panneau(designation: str) -> dict[str, str] | None:
    text = strip_pack(designation)
    blob = fold(text)
    if _blocked(blob):
        return None
    if any(word in blob for word in ("osb", "contreplaqu", "agglomere", "agglo", "mdf", "platre", "laine", "porte")):
        return None
    if "sandwich" in blob and "panneau" in blob:
        kind = "sandwich"
    elif blob.startswith("panneau") and ("alveolaire" in blob or "alveole" in blob):
        kind = "alveolaire"
    elif blob.startswith("panneau") and ("bois" in blob or "3 plis" in blob or "trois plis" in blob):
        kind = "bois"
    else:
        return None
    return {"kind": kind, "format": format_pair(text) or ""}


SPECS: tuple[dict[str, Any], ...] = (
    {"code": "CAT_PLINTHE", "label": "Plinthe", "category": "Revêtement", "unit": "pièce", "ilike": ("plinthe",), "keys": ("decor", "material", "format"), "parse": parse_plinthe},
    {"code": "CAT_STRATIFIE", "label": "Sol stratifié", "category": "Revêtement", "unit": "m²", "ilike": ("stratifié", "stratifie"), "keys": ("decor", "format", "usage"), "parse": parse_stratifie},
    {"code": "CAT_LAME", "label": "Lame de sol ou terrasse", "category": "Revêtement", "unit": "pièce", "ilike": ("lame",), "keys": ("kind", "material", "color", "format"), "parse": parse_lame},
    {"code": "CAT_CARRELAGE", "label": "Carrelage", "category": "Revêtement", "unit": "m²", "ilike": ("carrelage", "faïence", "faience", "grès", "gres"), "keys": ("usage", "material", "decor", "color", "finish", "format"), "parse": parse_carrelage},
    {"code": "CAT_PLAN", "label": "Plan de travail", "category": "Menuiserie", "unit": "pièce", "ilike": ("plan de travail", "plan travail"), "keys": ("material", "decor", "format"), "parse": parse_plan},
    {"code": "CAT_LAINE", "label": "Laine isolante", "category": "Isolation", "unit": "pièce", "ilike": ("laine", "ouate"), "keys": ("material", "thickness_mm", "form", "r_value", "format"), "parse": parse_laine},
    {"code": "CAT_PANNEAU", "label": "Panneau", "category": "Panneau", "unit": "pièce", "ilike": ("panneau", "sandwich"), "keys": ("kind", "format"), "parse": parse_panneau},
    {"code": "CAT_BOIS", "label": "Bois débité", "category": "Bois", "unit": "pièce", "ilike": ("tasseau", "chevron", "lambourde", "madrier", "bastaing", "volige"), "keys": ("kind", "essence", "section", "length"), "parse": parse_bois},
    {"code": "CAT_LIQUIDE", "label": "Peinture et finition", "category": "Peinture", "unit": "pièce", "ilike": ("peinture", "lasure", "vernis", "vitrificateur", "saturateur", "sous-couche", "sous couche"), "keys": ("kind", "usage", "finish", "color", "volume", "gamme"), "parse": parse_liquide},
    {"code": "CAT_ENDUIT", "label": "Enduit", "category": "Gros œuvre", "unit": "pièce", "ilike": ("enduit",), "keys": ("fonction", "form", "poids"), "parse": parse_enduit},
    {"code": "CAT_MORTIER", "label": "Mortier", "category": "Gros œuvre", "unit": "sac", "ilike": ("mortier",), "keys": ("kind", "poids"), "parse": parse_mortier},
    {"code": "CAT_COLLE", "label": "Colle", "category": "Gros œuvre", "unit": "pièce", "ilike": ("colle",), "keys": ("kind", "quantite"), "parse": parse_colle},
    {"code": "CAT_JOINT", "label": "Joint", "category": "Revêtement", "unit": "pièce", "ilike": ("joint",), "keys": ("kind", "color", "quantite"), "parse": parse_joint},
    {"code": "CAT_BLOC_PORTE", "label": "Bloc-porte", "category": "Menuiserie", "unit": "pièce", "ilike": ("bloc-porte", "bloc porte", "bloque porte"), "keys": ("kind", "width_cm", "sens", "color"), "parse": parse_bloc_porte},
    {"code": "CAT_FENETRE", "label": "Fenêtre", "category": "Menuiserie", "unit": "pièce", "ilike": ("fenêtre", "fenetre"), "keys": ("material", "opening", "format", "color"), "parse": parse_fenetre},
    {"code": "CAT_STORE", "label": "Store ou volet", "category": "Menuiserie", "unit": "pièce", "ilike": ("store", "volet"), "keys": ("kind", "format", "color"), "parse": parse_store},
    {"code": "CAT_POIGNEE", "label": "Poignée", "category": "Quincaillerie", "unit": "pièce", "ilike": ("poignée", "poignee"), "keys": ("kind", "finish", "entraxe", "modele"), "parse": parse_poignee},
    {"code": "CAT_SERRURE", "label": "Serrure", "category": "Quincaillerie", "unit": "pièce", "ilike": ("serrure", "cadenas"), "keys": ("kind", "axe", "sens"), "parse": parse_serrure},
    {"code": "CAT_MITIGEUR", "label": "Mitigeur", "category": "Sanitaire", "unit": "pièce", "ilike": ("mitigeur", "mélangeur", "melangeur"), "keys": ("usage", "finish", "modele"), "parse": parse_mitigeur},
    {"code": "CAT_SANITAIRE", "label": "Sanitaire", "category": "Sanitaire", "unit": "pièce", "ilike": ("receveur", "baignoire", "vasque", "cuvette", "wc", "colonne"), "keys": ("kind", "modele", "color", "format"), "parse": parse_sanitaire},
    {"code": "CAT_RADIATEUR", "label": "Radiateur", "category": "Chauffage", "unit": "pièce", "ilike": ("radiateur",), "keys": ("kind", "power_w", "color", "modele", "format"), "parse": parse_radiateur},
    {"code": "CAT_TUBE", "label": "Tube", "category": "Plomberie", "unit": "pièce", "ilike": ("tube",), "keys": ("material", "diameter_mm", "length"), "parse": parse_tube},
    {"code": "CAT_RACCORD", "label": "Raccord", "category": "Plomberie", "unit": "pièce", "ilike": ("raccord", "coude", "manchon", "collecteur"), "keys": ("kind", "material", "diameter_mm", "thread", "system"), "parse": parse_raccord},
    {"code": "CAT_APPAREILLAGE", "label": "Appareillage", "category": "Électricité", "unit": "pièce", "ilike": ("odace", "dooxie", "celiane", "niloe", "mosaïc", "mosaic", "plexo"), "keys": ("gamme", "kind", "pose", "color"), "parse": parse_appareillage},
    {"code": "CAT_FORET", "label": "Foret", "category": "Outillage", "unit": "pièce", "ilike": ("foret", "forêt"), "keys": ("material", "format"), "parse": parse_foret},
    {"code": "CAT_LUMINAIRE", "label": "Luminaire", "category": "Électricité", "unit": "pièce", "ilike": ("spot", "applique", "plafonnier", "réglette", "reglette"), "keys": ("kind", "power_w", "color", "format", "modele"), "parse": parse_luminaire},
    {"code": "CAT_MEUBLE", "label": "Meuble", "category": "Ameublement", "unit": "pièce", "ilike": ("meuble", "colonne"), "keys": ("kind", "collection", "color", "finish", "format"), "parse": parse_meuble},
    {"code": "CAT_FACADE", "label": "Façade", "category": "Ameublement", "unit": "pièce", "ilike": ("façade", "facade"), "keys": ("kind", "collection", "color", "finish", "format"), "parse": parse_facade},
    {"code": "CAT_KIT", "label": "Kit", "category": "Kit", "unit": "pièce", "ilike": ("kit ", "ensemble"), "keys": ("kind", "modele", "format", "color"), "parse": parse_kit},
    {"code": "CAT_PORTE_RANG", "label": "Porte de rangement", "category": "Menuiserie", "unit": "pièce", "ilike": ("porte coulissante", "porte de placard", "porte placard"), "keys": ("kind", "collection", "color", "format"), "parse": parse_porte_rang},
    {"code": "CAT_EQUERRE", "label": "Équerre", "category": "Quincaillerie", "unit": "pièce", "ilike": ("équerre", "equerre"), "keys": ("kind", "format"), "parse": parse_equerre},
    {"code": "CAT_DISQUE", "label": "Disque", "category": "Outillage", "unit": "pièce", "ilike": ("disque",), "keys": ("material", "diameter_mm"), "parse": parse_disque},
)

CATALOG_META: dict[str, dict[str, Any]] = {}


def _model(code: str, keys: tuple[str, ...]) -> IdentityModel:
    return IdentityModel(
        name=f"CATALOG_{code}",
        identity_keys=keys,
        product_key_map={key: key for key in keys},
        allow_high=False,
        partial_strategy=PARTIAL_HIERARCHY,
        hierarchy_keys=(keys[0],),
    )


def _register(spec: dict[str, Any]) -> CategoryRule:
    keys: tuple[str, ...] = spec["keys"]
    code = spec["code"]
    parse: ParseFn = spec["parse"]
    prefix = f"PMC-{code}-"

    def extract(designation: str, category_path: str | None = None) -> ExtractionResult:
        parsed = parse(designation)
        if parsed is None:
            return ExtractionResult(
                category_code=None,
                attributes={},
                confidence=0.0,
                extractor_version=f"{code.lower()}.v1",
                classified=False,
                reason=REASON_NOT,
            )
        attrs = {key: parsed.get(key) or None for key in keys}
        complete = all(attrs[key] not in (None, "") for key in keys)
        return ExtractionResult(
            category_code=code,
            attributes=attrs,
            confidence=0.9 if complete else 0.4,
            extractor_version=f"{code.lower()}.v1",
            classified=True,
            reason=None,
        )

    rule = register_rule(
        CategoryRule(
            code=code,
            category_name=spec["label"],
            identity=_model(code, keys),
            pmc_code_prefixes=(prefix,),
            designation_ilike=spec["ilike"],
            attribute_defs=tuple(
                {"key": key, "data_type": "string", "required": True, "match_role": "identity"}
                for key in keys
            ),
            reference_unit_default=spec["unit"],
            algorithm_version=f"{code.lower()}_match.v1",
            extractor_version=f"{code.lower()}.v1",
            extract=extract,
        )
    )
    CATALOG_META[code] = {**spec, "prefix": prefix, "keys": keys}
    return rule


CATALOG_RULES = tuple(_register(spec) for spec in SPECS)
CATALOG_CODES = tuple(spec["code"] for spec in SPECS)


def code_for(prefix: str, keys: tuple[str, ...], attrs: dict[str, Any]) -> str:
    raw = "|".join(str(attrs[key]) for key in keys)
    digest = hashlib.sha1(raw.encode()).hexdigest()[:8].upper()
    return f"{prefix}{digest}"


def _name_for(label: str, keys: tuple[str, ...], attrs: dict[str, Any]) -> str:
    bits = " ".join(str(attrs[key]).replace("_", " ") for key in keys if attrs.get(key) not in (None, "", "nr", "std"))
    return f"{label} {bits}".strip()[:200]


def seed_catalog_family(session: Session, category_code: str) -> dict[str, Any]:
    meta = CATALOG_META[category_code]
    rule = pipeline_rule(category_code)
    run = pipeline.run_category(session, rule, persist=False, all_candidates=True, limit=None)
    identities: dict[tuple[str, ...], dict[str, Any]] = {}
    for item in run.items:
        if not item.classified:
            continue
        if any(item.attrs.get(key) in (None, "") for key in meta["keys"]):
            continue
        signature = tuple(str(item.attrs[key]) for key in meta["keys"])
        identities[signature] = {key: item.attrs[key] for key in meta["keys"]}
    created = 0
    skipped = 0
    for signature, attrs in sorted(identities.items()):
        code = code_for(meta["prefix"], meta["keys"], attrs)
        existing = session.scalar(select(Product).where(Product.code == code))
        if existing is not None:
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=_name_for(meta["label"], meta["keys"], attrs),
                category=meta["category"],
                subcategory=meta["label"],
                reference_unit=meta["unit"],
                description=f"{meta['label']} — {' | '.join(f'{k}={attrs[k]}' for k in meta['keys'])}"[:400],
                attributes=attrs,
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
    if created:
        session.flush()
    return {"category": category_code, "identities_found": len(identities), "created": created, "skipped_existing": skipped}


def pipeline_rule(code: str) -> CategoryRule:
    from app.services.product_mapping.rules.base import get_rule

    return get_rule(code)


def brico_counts(session: Session) -> dict[str, int]:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == "BRICO_DEPOT"))
    total = int(
        session.scalar(
            select(func.count()).select_from(SupplierProduct).where(SupplierProduct.supplier_id == supplier_id)
        )
        or 0
    )
    mapped = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(SupplierProduct.supplier_id == supplier_id, SupplierProduct.product_id.is_not(None))
        )
        or 0
    )
    manual = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(SupplierProduct.supplier_id == supplier_id, SupplierProduct.correction_source == "manual")
        )
        or 0
    )
    exact_rule = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(SupplierProduct.supplier_id == supplier_id, SupplierProduct.correction_source == "exact_rule")
        )
        or 0
    )
    return {"total": total, "mapped": mapped, "unmapped": total - mapped, "manual": manual, "exact_rule": exact_rule}


def _snapshot(session: Session) -> dict[int, tuple[int, str | None]]:
    rows = session.execute(
        select(SupplierProduct.id, SupplierProduct.product_id, SupplierProduct.correction_source).where(
            SupplierProduct.product_id.is_not(None)
        )
    ).all()
    return {int(row[0]): (int(row[1]), row[2]) for row in rows}


def _assert_preserved(session: Session, before: dict[int, tuple[int, str | None]]) -> None:
    rows = session.execute(
        select(SupplierProduct.id, SupplierProduct.product_id, SupplierProduct.correction_source).where(
            SupplierProduct.id.in_(list(before))
        )
    ).all()
    found = {int(row[0]): (row[1], row[2]) for row in rows}
    for sp_id, (product_id, source) in before.items():
        current = found.get(sp_id)
        if current is None or current[0] != product_id:
            raise RuntimeError(f"mapping existant modifié: sp={sp_id}")
        if source in ("manual", "exact_rule") and current[1] != source:
            raise RuntimeError(f"correction_source modifié: sp={sp_id}")


def leftover_heads(session: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == "BRICO_DEPOT"))
    rows = session.execute(
        select(SupplierProduct.designation).where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.product_id.is_(None),
        )
    ).all()
    counts: dict[str, int] = defaultdict(int)
    for (designation,) in rows:
        tokens = fold(designation or "").split()
        counts[tokens[0] if tokens else "?"] += 1
    ranked = sorted(counts.items(), key=lambda item: -item[1])[:limit]
    return [{"famille": name, "sp": count} for name, count in ranked]


def run_catalog(session: Session, *, apply: bool = True) -> dict[str, Any]:
    import app.services.product_mapping.rules  # noqa: F401
    from app.services.product_mapping.mass_batch import apply_if_clean, audit_exact_matches

    before = brico_counts(session)
    snapshot = _snapshot(session)
    products_before = int(session.scalar(select(func.count()).select_from(Product)) or 0)
    report: dict[str, Any] = {"before": before, "families": []}
    for code in CATALOG_CODES:
        family: dict[str, Any] = {"category": code}
        family["seed"] = seed_catalog_family(session, code)
        session.flush()
        family["audit"] = audit_exact_matches(session, code)
        if apply and family["audit"]["mismatch_count"] == 0:
            family["apply"] = apply_if_clean(session, code)
            session.flush()
            family["seed_second"] = seed_catalog_family(session, code)
            family["apply_second"] = apply_if_clean(session, code)
        else:
            family["apply"] = {"applied": 0, "skipped_reason": "exact_mismatch" if apply else "preview"}
            family["seed_second"] = {"created": 0 if not apply else None}
            family["apply_second"] = {"applied": 0 if not apply else None}
        report["families"].append(family)
    if apply:
        failed = [f["category"] for f in report["families"] if f["audit"]["mismatch_count"]]
        if failed:
            session.rollback()
            raise RuntimeError(f"identité en écart: {failed}")
        _assert_preserved(session, snapshot)
        for family in report["families"]:
            if (family.get("seed_second") or {}).get("created") not in (0, None):
                session.rollback()
                raise RuntimeError(f"seed non idempotent: {family['category']}")
            if (family.get("apply_second") or {}).get("applied") not in (0, None):
                session.rollback()
                raise RuntimeError(f"apply non idempotent: {family['category']}")
        session.commit()
    else:
        session.rollback()
    report["after"] = brico_counts(session)
    report["products_before"] = products_before
    report["products_after"] = int(session.scalar(select(func.count()).select_from(Product)) or 0)
    report["pmc_created"] = report["products_after"] - products_before
    report["leftover"] = leftover_heads(session)
    return report
