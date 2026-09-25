"""Parcours simplifié : identité compacte, choix → redirection fiche."""

from pathlib import Path

from app.version import APP_VERSION


def test_detail_identity_is_compact_by_default():
    detail = Path("app/templates/chantier_detail.html").read_text(encoding="utf-8")
    assert 'id="identity-compact"' in detail
    assert 'id="identity-edit-panel"' in detail
    assert 'hidden' in detail.split('id="identity-edit-panel"', 1)[1].split(">", 1)[0]
    assert "Modifier les informations" in detail
    assert 'id="identity-cancel"' in detail
    assert 'id="identity-save"' in detail
    assert "Comparer les prix" in detail
    assert "Enregistrer les besoins" in detail
    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "showIdentityCompact" in js
    assert "showIdentityEdit" in js
    assert "cancelIdentityEdit" in js
    assert "identitySnapshot" in js
    assert 'params.get("approvisionnement") === "retenu"' in js
    assert "Solution d’approvisionnement enregistrée." in js


def test_comparator_choice_redirects_to_chantier_after_success():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "Enregistrer la liste" in Path("app/templates/index.html").read_text(
        encoding="utf-8"
    )
    assert "Comparaison pour :" in Path("app/templates/index.html").read_text(
        encoding="utf-8"
    )
    assert "Met à jour les besoins du chantier sans choisir de fournisseur." in Path(
        "app/templates/index.html"
    ).read_text(encoding="utf-8")
    submit = app_js.split('$("choice-confirm-submit").addEventListener("click"', 1)[1]
    assert "window.location.assign(" in submit
    assert "?approvisionnement=retenu" in submit
    assert "response.status === 409" in submit
    # Pas de redirection avant le succès.
    fail_block = submit.split("if (response.status === 409)", 1)[1].split(
        "window.location.assign", 1
    )[0]
    assert "return;" in fail_block


def test_choice_confirm_copy_is_compact():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "Confirmer cette solution pour « ${loadedChantier.nom} » ?" in app_js
    assert "Total estimé :" in app_js
    home = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert 'id="choice-confirm-submit"' in home
    assert "Confirmer et retourner au chantier" in home
    assert 'id="comparator-return-link"' in home
    assert "Après confirmation, retour automatique au chantier." in home
    assert APP_VERSION == "0.4.0"
