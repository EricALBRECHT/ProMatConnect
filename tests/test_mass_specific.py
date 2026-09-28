"""Fallback spécifique : même désignation = même clé, SKU hors attribut métier."""

from app.services.product_mapping.mass_specific import designation_key, specific_code


def test_specific_key_shares_exact_text_only():
    assert designation_key("  Peinture  Blanche ") == designation_key("peinture blanche")
    assert designation_key("Peinture blanche 10 L") != designation_key("Peinture blanche 2 L")
    assert specific_code("peinture blanche") == specific_code("peinture blanche")
    assert specific_code("peinture blanche") != specific_code("peinture blanche 10 l")
    assert designation_key("") == ""
    assert not specific_code("peinture blanche").startswith("PMC-SPEC-sku")
