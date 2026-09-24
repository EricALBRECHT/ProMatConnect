"""Non-régression : un cleanup navigateur ne touche que les IDs qu'il a créés."""

from pathlib import Path

from tests.browser_cleanup_policy import ChantierCleanupTracker


def test_browser_cleanup_helper_exists_and_is_id_based():
    source = Path("tests/browser_cleanup.cjs").read_text(encoding="utf-8")
    assert "createdChantierIds" in source
    assert "track(" in source
    assert "cleanup(" in source
    assert "find((c) => c.nom" not in source
    assert "DELETE FROM" not in source
    e2e = Path("tests/browser_chantier_to_compare.cjs").read_text(encoding="utf-8")
    assert "createChantierTracker" in e2e
    assert "tracker.track(" in e2e
    assert "tracker.cleanup(" in e2e
    assert "list.find((c) => c.nom" not in e2e


def test_cleanup_policy_preserves_preexisting_chantiers():
    preexisting = {10: "Rufin", 11: "dupont"}
    tracker = ChantierCleanupTracker()
    # Aucune création → cleanup no-op.
    assert tracker.cleanup_ids() == []
    assert set(preexisting) == {10, 11}

    created = tracker.track(99)
    assert created == 99
    assert tracker.cleanup_ids() == [99]
    # Les préexistants ne sont jamais dans la liste de suppression.
    assert 10 not in tracker.cleanup_ids()
    assert 11 not in tracker.cleanup_ids()
    remaining = set(preexisting) | {99}
    remaining -= set(tracker.cleanup_ids())
    assert remaining == set(preexisting)


def test_cleanup_policy_ignores_name_based_deletion():
    tracker = ChantierCleanupTracker()
    tracker.track(42)
    # Un nom générique ne peut pas étendre la liste des IDs à supprimer.
    assert tracker.ids_matching_name({"42": "TEST", "7": "Rufin"}, "TEST") == []
    assert tracker.cleanup_ids() == [42]
