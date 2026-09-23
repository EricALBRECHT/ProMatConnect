# Validation de la livraison 0.2

Vérification du 23 septembre 2026, dans `/home/pi/Batiplus/App`, WSL/Linux,
Python 3.12.3. Aucun commit ni push effectué. Les modèles SQL, le seed, les
connecteurs fournisseurs et les 52 tests historiques sont conservés sans modification.

## Avant modification

Commande `.venv/bin/pytest -q` exécutée avant toute édition :

```text
52 passed in 8.20s
```

## Après modification

| Vérification | Résultat |
| --- | --- |
| Pytest local, SQLite temporaire | **113 passed in 17.03s**, aucun avertissement |
| Pytest dans Docker, PostgreSQL 16 | **113 passed in 30.49s**, aucun avertissement |
| Nombre de tests distincts ajoutés | **61** (113 au total, avec paramétrages) |
| Ruff | `All checks passed!` |
| Format Python | `39 files already formatted` |
| Docker Compose | Configuration valide |
| Images Docker `web` et `tests` | Build réussi |
| `docker compose up --build -d --wait web` | Réussi ; web et PostgreSQL sains |
| `/api/health` | `status=ok`, `version=0.2.0` |
| Chromium, 1440 × 1100 | Parcours complet réussi |
| Chromium, 390 × 844 | Parcours complet réussi, cartes verticales, sans débordement |

Les mêmes 113 tests sont exécutés sur deux moteurs, pas 226 tests différents.
Les tests PostgreSQL utilisent exclusivement la base jetable `promatconnect_test`,
séparée de la base applicative. Le volume applicatif est conservé. Aucun service
fournisseur, cartographique ou géocodage externe n'est appelé par ces tests.

## Contrôles des décisions et de l'interface

Les nouveaux tests couvrent les quatre origines, les coordonnées et leurs bornes,
les adresses inconnues, les services remplaçables par injection, le trajet dirigé
aller-retour, les segments entre agences, l'ordre optimal à 2/3 arrêts, l'algorithme
pour 4 arrêts comparé aux permutations, le coût kilométrique, le temps avec préparation,
la pénalité au-delà du premier arrêt, les coûts négatifs rejetés, les arrondis,
les stocks insuffisants, le vrai arrêt unique et les résultats API.

Un test compare le compromis à une énumération indépendante de toutes les
répartitions d'un petit panier. Un autre vérifie qu'une référence plus chère,
mais prête plus tôt, peut gagner dans la même agence. Le compromis peut aussi
retenir plusieurs fournisseurs. Les limites de taille renvoient une erreur explicite.

Le panier d'exemple a été lancé sur l'application Docker, son JSON examiné, les
captures desktop/mobile inspectées visuellement. Le script conservé dans
`tests/browser_smoke.cjs` vérifie aussi :

- ajout, modification, suppression et recherche de matériaux ;
- trois stratégies et détails du trajet, calcul et matériaux par agence ;
- absence d'appel GPS au chargement ou au choix Entreprise ;
- appel GPS seulement après choix Ma position / clic d'actualisation ;
- refus d'autorisation, position reçue et réponse GPS tardive ignorée après changement ;
- erreur d'adresse inconnue, changement d'origine et invalidation des anciens résultats ;
- produit totalement indisponible sans coût total trompeur ;
- absence d'erreurs JavaScript et de débordement horizontal.

## Panier d'exemple constaté

Départ : `10 rue du Chantier, 75004 Paris`. Quantités : 30 BA13, 10 rails R48,
20 montants M48. Paramètres : 0,50 €/km, 30 €/heure, 5 €/arrêt supplémentaire.

| Stratégie | Matériaux HT | Arrêts | Distance A/R | Trajet | Préparation | Coût estimé non facturé |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 seul arrêt | 380,20 € | 1 | 13,80 km | 45,40 min | 150 min | 484,80 € |
| Prix matériaux minimum | 375,40 € | 2 | 15,33 km | 51,99 min | 150 min | 489,07 € |
| Meilleur compromis | 377,40 € | 3 | 21,48 km | 72,44 min | 90 min | 479,36 € |

Le compromis choisit Chantier → GEDIMAT TEST Paris Est → POINT.P TEST Paris Est →
GEDIMAT TEST Ivry → Chantier. Il nécessite davantage de trajet mais moins de
préparation. Temps total selon l'hypothèse documentée : 162,44 minutes contre
195,40 minutes pour un seul arrêt.

Formule : matériaux + arrondi(km × 0,50) + arrondi((trajet + préparation maximale)
× 30 / 60) + arrondi(max(arrêts − 1, 0) × 5). Arrondis au centime `ROUND_HALF_UP`
séparés par composante. Pour le compromis : 377,40 + 10,74 + 81,22 + 10 = 479,36 €.

## Fichiers créés et modifiés

Créés :

- `app/schemas/location.py`
- `app/services/geocoding.py`
- `app/services/origin.py`
- `app/services/routing.py`
- `app/services/procurement_cost.py`
- `app/services/optimization.py`
- `tests/test_origins.py`
- `tests/test_routing.py`
- `tests/test_procurement.py`
- `tests/test_api_v02.py`
- `tests/browser_smoke.cjs`

Modifiés :

- `app/config.py`, `app/main.py`, `app/routes/api.py`
- `app/schemas/comparison.py`, `app/services/comparison.py`
- `app/templates/index.html`, `app/static/app.js`, `app/static/styles.css`
- `.env.example`, `docker-compose.yml`, `README.md`, `VALIDATION.md`

Aucune dépendance runtime ajoutée, aucun changement de modèle SQL. `.env` personnel
conservé. L'ancien contrat `options` reste disponible ; `strategies` porte les trois
résultats V0.2. Les règles et limitations complètes sont dans `README.md`.

## Limites restantes

Aucun blocage technique constaté sur la livraison. Le géocodage reconnaît seulement
les adresses de démonstration et le routage est simulé. La préparation est supposée
parallèle et attendue intégralement avant le départ. La recherche est bornée à
8 agences par défaut. Il n'y a pas encore de carte, trafic, horaires, navigation,
mode Express ou service fournisseur réel.

Contrairement au contrôle V0.1, Docker est désormais accessible et a été vérifié
avec succès. L'application V0.2 reste démarrée sur http://localhost:8000 ; Swagger
est disponible sur http://localhost:8000/docs.
