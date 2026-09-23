# Validation de la livraison 0.1

Vérification effectuée le 23 septembre 2026 dans `/home/pi/Batiplus/App`, sous
WSL/Linux, avec Python 3.12.3. Aucun commit ni push Git effectué.

## Résultats obtenus

| Vérification | Résultat |
| --- | --- |
| Pytest, SQLite temporaire | **52 passed in 6.83s**, sans avertissement |
| Même suite, PostgreSQL 16.15 local temporaire | **52 passed in 12.53s**, sans avertissement |
| Ruff | `All checks passed!` |
| Format Python | `29 files already formatted` |
| Cohérence des dépendances | `No broken requirements found.` |
| Compilation des modules Python | Réussie |
| Syntaxe JavaScript (`node --check`) | Réussie |
| Navigateur Chromium, 1440 × 1100 | Parcours réussi, aucune erreur JavaScript |
| Navigateur Chromium, 390 × 844 | Parcours réussi, aucune erreur JavaScript ni débordement horizontal |
| Configuration Docker Compose v2.39.2 | `config --quiet` : code de sortie 0 |
| Build de l'image Docker | **Bloqué : moteur Docker inaccessible** |
| Démarrage des conteneurs / healthchecks Docker | Non exécuté, même blocage |

Il s'agit de **52 tests distincts exécutés sur deux moteurs de base**, pas de
104 tests différents. L'instance PostgreSQL de validation a été démarrée avec
des binaires locaux temporaires, sans modifier un serveur existant.
Les outils de navigateur et de validation Docker ont été téléchargés hors du projet.

Le navigateur a vérifié : chargement du catalogue, ajout du panier d'exemple,
comparaison des trois options, édition d'une quantité et invalidation des résultats,
suppression, recherche du mastic, ajout d'un produit indisponible, affichage des
sous-totaux et ouverture du détail. Le défaut de débordement détecté sur mobile a
été corrigé puis le parcours complet a été rejoué avec succès.

Résultats du panier d'exemple (30 BA13, 10 rails, 20 montants) :

| Option | Prix HT | Fournisseurs | Arrêts |
| --- | ---: | ---: | ---: |
| POINT.P TEST | 439,10 € | 1 | 2 |
| GEDIMAT TEST | 380,20 € | 1 | 1 |
| Panier optimisé | 375,40 € | 2 | 2 |

## Blocage Docker restant

La commande de build a réellement été tentée avec un client Docker temporaire :

```text
docker build --target runtime -t promatconnect:0.1 .
Cannot connect to the Docker daemon at unix:///var/run/docker.sock.
Is the docker daemon running?
```

Le client Docker fourni par l'environnement signale lui aussi que l'intégration
Docker Desktop n'est pas active dans cette distribution WSL. Le socket Docker est
absent. Aucun build réussi n'est donc revendiqué ; les tests PostgreSQL locaux et
la validation syntaxique Compose ne le remplacent pas.

Après activation de Docker Desktop et de son intégration WSL :

```bash
cd /home/pi/Batiplus/App
cp .env.example .env
docker compose up --build
docker compose ps
```

L'application doit être accessible sur http://localhost:8000 et Swagger sur
http://localhost:8000/docs. Les commandes de test PostgreSQL dans Docker sont
documentées dans `README.md`.
