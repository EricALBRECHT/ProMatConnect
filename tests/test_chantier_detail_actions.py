"""Non-régression : actions primaires du détail chantier restent branchées."""

from pathlib import Path


def test_detail_primary_actions_bound_before_secondary_appro():
    """Comparer / Supprimer doivent être bindés avant les handlers appro."""
    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    compare_pos = js.index('$("compare-prices").addEventListener("click"')
    delete_pos = js.index('$("delete-chantier").addEventListener("click"')
    # Les handlers appro sont optionnels (guard if) et après les actions primaires.
    appro_pos = js.index('const approRecompare = $("appro-recompare")')
    assert compare_pos < appro_pos
    assert delete_pos < appro_pos
    assert "window.location.assign(`/?chantier_id=${chantierId}`)" in js
    assert "delete-chantier-panel" in js
    assert "loadApprovisionnement" in js
    # Un échec d'affichage appro ne doit pas faire échouer la sauvegarde.
    save_block = js.split("async function saveChantier", 1)[1].split(
        '$("save-button").addEventListener', 1
    )[0]
    assert "await loadApprovisionnement()" in save_block
    assert "Approvisionnement après sauvegarde" in save_block
    assert 'addEventListener("click", goCompare)' in js


def test_detail_template_has_primary_and_appro_hooks(client):
    page = client.get("/chantiers/nouveau")
    # Créer via API puis ouvrir détail
    created = client.post(
        "/api/chantiers",
        json={
            "nom": "Hooks détail",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [{"product_id": 1, "quantite": "2", "ordre": 0}],
        },
    ).json()
    detail = client.get(f"/chantiers/{created['id']}").text
    for needle in (
        'id="compare-prices"',
        'id="save-button"',
        'id="delete-chantier"',
        'id="delete-chantier-panel"',
        'id="appro-empty"',
        'id="appro-recompare"',
        "chantiers.js",
        "materials.js",
    ):
        assert needle in detail
