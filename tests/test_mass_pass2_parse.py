"""Parseur câble de la passe 2 — cas qui séparent norme, couleur et conducteurs."""

from app.services.product_mapping.mass_pass2 import parse_cable_identity


def test_cable_identity_splits_norm_color_and_conductors():
    assert parse_cable_identity("Fil électrique H07VU 1,5 mm² bleu - 100 m") == {
        "kind": "h07vu",
        "section_mm2": "1.5",
        "conductors": 1,
        "length_m": 100,
        "color": "bleu",
    }
    assert parse_cable_identity("Fil H07VU 1,5 mm² vert/jaune 100 m")["color"] == "vert_jaune"
    assert parse_cable_identity("Câble R2V 3G1,5 mm² noir - 100 m") == {
        "kind": "r2v",
        "section_mm2": "1.5",
        "conductors": 3,
        "length_m": 100,
        "color": "noir",
    }
    assert parse_cable_identity("Câble H05VVF 3G1,5 mm² blanc - 10 m")["kind"] == "h05vvf"
    assert parse_cable_identity("Câble HIFI 2x1,5 mm² 10 m") is None
    assert parse_cable_identity("Fil H07VU 1,5 mm² vert/jaune/rouge/bleu 100 m") is None
    assert parse_cable_identity("Câble R2V 3G1,5 mm² 100 m") is None
