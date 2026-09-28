"""Quelques identités catalogue : format, décor, et système de raccord."""

from app.services.product_mapping.mass_catalog import (
    format_pair,
    parse_carrelage,
    parse_liquide,
    parse_raccord,
    parse_stratifie,
)


def test_format_keeps_unit_and_sorted_sides():
    assert format_pair('l. 30,8 x L. 61,5 cm') == "615x308"
    assert format_pair("L.138,3 x l.19,3 cm x Ép. 7mm", loose_thickness=True) == "1383x193x7"
    assert format_pair("65 x 117 x 52 mm") == "117x65x52"


def test_tile_and_laminate_keep_decor():
    tile = parse_carrelage('Carrelage sol intérieur grès cérame émaillé beige aspect pierre ROMA 31 x 62 cm')
    other = parse_carrelage('Carrelage sol intérieur grès cérame émaillé beige aspect pierre CASTELLO 31 x 62 cm')
    assert tile["decor"] == "roma"
    assert other["decor"] == "castello"
    assert tile["format"] == other["format"]
    floor = parse_stratifie('Sol stratifié aspect chêne "Feticeira" L. 128,5 x l. 28 cm x Ép. 8 mm')
    assert floor["decor"] == "feticeira"
    assert floor["format"] == "1285x280x8"


def test_paint_identity_and_fitting_system():
    paint = parse_liquide('Peinture mat mur et plafond blanc pur "Valspar pro" - pot 10 L')
    assert paint["finish"] == "mat"
    assert paint["usage"] == "mur_plafond"
    assert paint["color"] == "blanc_pur"
    assert paint["volume"] == "10l"
    assert paint["gamme"] == "valspar pro"
    bicone = parse_raccord("Coude égal bicône à bague laiton pour tube cuivre Ø14")
    solder = parse_raccord("Coude à souder pour tube cuivre Ø14")
    assert bicone["system"] == "bicone"
    assert solder["system"] == "souder"
    assert bicone["diameter_mm"] == solder["diameter_mm"] == "14"
