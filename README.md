# ProMatConnect — MVP technique 0.2

Optimiseur d'approvisionnement B2B pour les artisans du bâtiment. Constituez une liste,
choisissez votre point de départ et comparez **1 seul arrêt**, **Prix matériaux minimum**
et **Meilleur compromis**. L'interface conserve aussi la comparaison par enseigne V0.1
sous un volet dépliable ; le contrat historique `options` reste présent dans l'API.

Toutes les agences, références, disponibilités, offres et données routières sont **simulées**.
Aucun scraping, appel d'API fournisseur, compte utilisateur, paiement, commande,
facturation, gestion de chantiers ou livraison n'est intégré. Les prix sont en **EUR HT**.

## Démarrage avec Docker

Prérequis : Docker Engine / Docker Desktop démarré et Docker Compose v2.
Sous WSL, activer l'intégration de la distribution dans Docker Desktop.

```bash
cd /home/pi/Batiplus/App
test -f .env || cp .env.example .env
docker compose up --build
```

Le chemin ci-dessus correspond au dossier de cette livraison ; adapter si déplacé.
Pour une mise à jour V0.1 → V0.2, conserver `.env` et le volume existants. Ajouter les
nouveaux paramètres de `.env.example` seulement pour changer les valeurs par défaut.
Le schéma SQL et les données fournisseurs ne changent pas ; aucune migration n'est requise.
Le démarrage fonctionne aussi sans `.env` avec les valeurs de démonstration de Compose.
PostgreSQL doit devenir sain avant le démarrage de l'application. Les tables puis le
seed sont initialisés automatiquement. Attendre le message de démarrage d'Uvicorn.

- Application : http://localhost:8000
- Swagger : http://localhost:8000/docs
- Schéma OpenAPI : http://localhost:8000/openapi.json
- Santé de l'application et de PostgreSQL : http://localhost:8000/api/health

Commandes utiles :

```bash
docker compose ps
docker compose logs -f web
docker compose exec web python -m scripts.seed
docker compose down
```

`down` conserve le volume `postgres_data`. Le seed est réexécutable sans doublon et
ne remplace pas les données existantes. Pour réinitialiser **volontairement toutes
les données locales**, `docker compose down -v`, puis `docker compose up --build`.

Les identifiants `local_demo_only` sont des valeurs publiques de développement,
pas des secrets de production. `.env` est exclu de Git et du contexte Docker.
Le port web est lié à `127.0.0.1` ; PostgreSQL n'est pas exposé sur l'hôte.
Le conteneur applicatif s'exécute avec un utilisateur non privilégié.

## Configuration

| Variable | Défaut | Usage |
| --- | --- | --- |
| `POSTGRES_DB` | `promatconnect` | Base Compose |
| `POSTGRES_USER` | `promatconnect` | Utilisateur Compose |
| `POSTGRES_PASSWORD` | `local_demo_only` | Mot de passe local Compose |
| `APP_PORT` | `8000` | Port HTTP exposé sur localhost |
| `USER_LATITUDE` | `48.8566` | Origine de compatibilité API sans `origin`, de -90 à 90 |
| `USER_LONGITUDE` | `2.3522` | Origine de compatibilité API sans `origin`, de -180 à 180 |
| `SEED_ON_START` | `true` | Insérer les données de démonstration manquantes |
| `DATABASE_URL` | URL PostgreSQL locale | Connexion SQLAlchemy hors Docker |
| `SITE_ADDRESS` | `10 rue du Chantier, 75004 Paris` | Adresse de chantier préremplie |
| `COMPANY_ADDRESS` | `20 rue de l'Entreprise, 75011 Paris` | Libellé local de l'entreprise |
| `COMPANY_LATITUDE` | `48.8600` | Coordonnée de l'entreprise |
| `COMPANY_LONGITUDE` | `2.3800` | Coordonnée de l'entreprise |
| `COST_PER_KM` | `0.50` | Indicateur EUR/km |
| `TIME_VALUE_PER_HOUR` | `30.00` | Valeur indicative EUR/heure |
| `EXTRA_STOP_COST` | `5.00` | Pénalité indicative par arrêt au-delà du premier |
| `OPTIMIZER_MAX_AGENCIES` | `8` | Limite de recherche exhaustive, configurable de 1 à 10 |

Compose construit `DATABASE_URL` à partir des variables PostgreSQL ; la valeur de
`.env` sert à l'exécution Python hors Docker. Si le mot de passe contient des
caractères réservés d'URL, adapter la configuration de connexion avec leur encodage.
Les changements d'identifiants PostgreSQL ne reconfigurent pas un volume déjà créé.

## Architecture

```text
.
├── app/
│   ├── main.py                 # Fabrique FastAPI, lifespan, HTML, erreurs
│   ├── config.py               # Configuration validée par Pydantic
│   ├── database.py             # Moteur SQLAlchemy et base déclarative
│   ├── models/__init__.py      # Cinq entités, relations et contraintes SQL
│   ├── schemas/
│   │   ├── catalog.py          # Produits exposés par l'API
│   │   ├── comparison.py       # Panier, compatibilité et trois stratégies
│   │   └── location.py         # Origine, coordonnées, segments et tournée
│   ├── repositories/
│   │   ├── catalog.py          # Accès ORM au catalogue interne
│   │   └── offers.py           # Mapping des références et lecture des offres
│   ├── connectors/
│   │   ├── base.py             # Interface et format normalisé des offres
│   │   ├── fake_base.py        # Lecture locale commune aux simulateurs
│   │   ├── fake_pointp.py
│   │   ├── fake_gedimat.py
│   │   └── registry.py         # Assemblage des connecteurs
│   ├── services/
│   │   ├── comparison.py       # Comparaison V0.1 et orchestration V0.2
│   │   ├── optimization.py     # Sous-ensembles d'agences et seuils de préparation
│   │   ├── procurement_cost.py # Formule et paramètres financiers indicatifs
│   │   ├── origin.py           # Résolution des quatre types de départ
│   │   ├── geocoding.py        # Interface et faux géocodeur déterministe
│   │   ├── routing.py          # Interface, grille simulée et ordre des arrêts
│   │   └── distance.py         # Haversine conservé pour les champs historiques
│   ├── routes/api.py          # Catalogue, comparaison, santé
│   ├── templates/index.html   # Page responsive Jinja2
│   └── static/
│       ├── app.js             # Panier et rendu des résultats, sans framework
│       └── styles.css
├── tests/
│   ├── conftest.py
│   ├── test_catalog.py
│   ├── test_connectors.py
│   ├── test_comparison.py
│   ├── test_api.py
│   ├── test_api_v02.py
│   ├── test_origins.py
│   ├── test_routing.py
│   ├── test_procurement.py
│   └── browser_smoke.cjs       # Parcours navigateur optionnel
├── scripts/seed.py
├── Dockerfile                 # Cibles runtime et test
├── docker-compose.yml         # PostgreSQL, web et profil de tests
├── requirements.txt
├── requirements-dev.txt
├── constraints.txt            # Versions résolues et validées
├── pyproject.toml             # Pytest et Ruff
├── .env.example
├── .dockerignore
├── .gitignore
├── VALIDATION.md
└── README.md
```

Flux : interface → routes/Pydantic → catalogue + connecteurs → service de
comparaison → réponse typée → cartes HTML. Les repositories sont les seuls modules
qui réalisent les lectures métier via l'ORM. Les simulateurs récupèrent les offres
stockées localement ; le comparateur consomme uniquement `SupplierConnector` et
`ConnectorOffer`. Il peut être testé avec des connecteurs en mémoire, sans base.

Le cycle de vie suit le mécanisme [lifespan de FastAPI](https://fastapi.tiangolo.com/advanced/events/).
Les modèles utilisent l'API déclarative typée [SQLAlchemy 2](https://docs.sqlalchemy.org/en/20/orm/quickstart.html).

## Modèle de données

| Entité | Champs métier |
| --- | --- |
| `Product` | `id`, `code` PMC unique, `name`, `category`, `reference_unit`, `description` |
| `Supplier` | `id`, `name` unique |
| `Agency` | `id`, `supplier_id`, `name`, `address`, `postal_code`, `city`, `latitude`, `longitude` |
| `SupplierProduct` | `id`, `product_id`, `supplier_id`, `supplier_reference`, `designation`, `supplier_unit`, `reference_quantity`, `active` |
| `Offer` | `id`, `supplier_id`, `supplier_product_id`, `agency_id`, `price`, `stock`, `preparation_minutes` |

Toutes les entités ont `created_at` et `updated_at`. Les dates des offres de
simulation sont fixées au 15 janvier 2026 à 08:00 UTC et ne changent pas au démarrage.
Les timestamps des autres entités reflètent leur création/modification réelle.

Relations : un produit PMC possède plusieurs références fournisseurs ; un fournisseur
possède plusieurs agences ; une offre associe une référence fournisseur à une agence.
Des clés étrangères composites imposent leur appartenance au même fournisseur.
Les index, unicités et contraintes interdisent les doublons d'offres, les prix/stocks
négatifs et les facteurs de conversion nuls ou négatifs.

- `price` : `Numeric(12, 2)` / `Decimal`, prix HT d'un conditionnement fournisseur.
- `stock` : entier, nombre de conditionnements disponibles dans l'agence.
- `reference_quantity` : `Numeric(12, 3)`, nombre d'unités PMC dans ce conditionnement.
- Quantités du panier : `Decimal`, de 0.001 à 1 000 000, au plus 3 décimales.
- Coordonnées : stockées en `Numeric(9, 6)` ; calcul géographique en flottants.
- Montants et quantités décimales sérialisés en chaînes JSON pour conserver la précision.
  Le navigateur formate les montants renvoyés ; il ne recalcule aucun prix.

Exemple : une boîte de 100 vis à 2,76 € HT a `reference_quantity=100`,
`supplier_unit="lot de 100 vis"`, `price=2.76`. Pour 150 vis, achat de deux boîtes,
soit 200 vis et 5,52 € HT. Il faut un stock d'au moins deux boîtes.

## Données déterministes

Le seed crée **20 produits, 2 fournisseurs, 6 agences, 40 références et 120 offres**.
Les valeurs sont calculées à partir des indices des produits et agences, sans hasard.
Les localisations sont fictives dans la région parisienne.

Scénarios à essayer :

- Bouton **Charger le panier d'exemple** : 30 BA13, 10 rails R48, 20 montants M48.
- Vis (`PMC0007`), laine de verre (`PMC0004`), PER (`PMC0009`) : conditionnements différents.
- OSB (`PMC0018`) : indisponible chez POINT.P TEST, disponible chez GEDIMAT TEST.
- Mastic (`PMC0020`) : indisponible chez les deux fournisseurs.
- Quantité très élevée : stock insuffisant, panier explicitement incomplet.

Le seed est transactionnel, utilise des clés stables et prend un verrou transactionnel
PostgreSQL pour sérialiser d'éventuels démarrages simultanés. Il n'écrase pas les
modifications existantes. La création initiale du schéma reste prévue pour une seule
instance applicative, comme dans ce Compose.

## Origine et géocodage

Le choix initial est **Chantier**, avec une adresse modifiable. **Autre adresse**
possède son propre champ libre. Le géocodeur fictif reconnaît uniquement ces adresses,
proposées par le champ de saisie (casse, accents et ponctuation normalisés) :

- `10 rue du Chantier, 75004 Paris` ;
- `20 rue de l'Entreprise, 75011 Paris` ;
- `5 rue des Artisans, 94200 Ivry-sur-Seine` ;
- `8 rue du Depot, 93200 Saint-Denis`.

Une adresse inconnue donne une erreur 422 lisible. Aucun service réseau ni position
par défaut cachée ne remplace une adresse saisie. **Entreprise** utilise directement
l'adresse et les coordonnées configurées ; les modifier ensemble pour les garder cohérentes.

**Ma position** déclenche `navigator.geolocation.getCurrentPosition` uniquement après
le choix volontaire de cette option ou un clic sur son bouton d'actualisation. Aucune
requête GPS au chargement. Le navigateur gère la permission ; refus, indisponibilité
et expiration sont affichés sans substituer une fausse position. Cette fonction
nécessite un contexte navigateur sécurisé (HTTPS ou localhost). Une réponse GPS
tardive est ignorée si l'utilisateur a entre-temps choisi une autre origine.
Les coordonnées sont transmises dans le corps de la comparaison, pas dans l'URL,
et ne sont enregistrées ni en base, ni dans le stockage local du navigateur.

`OriginService` peut être réutilisé par un futur mode Express avec
`OriginRequest(type="current_location", latitude=..., longitude=...)`, une fois les
coordonnées autorisées obtenues. Aucun mode Express n'est implémenté ici.

## Trajets simulés et ordre des arrêts

`RoutingService.leg(start, end)` fournit distance et durée d'un segment dirigé.
`route(origin, stops)` assemble la boucle **origine → agences → origine**. Les
segments inter-agences sont calculés : on n'additionne pas les distances origine-agence.
`RouteOrderOptimizer` est une interface distincte qui choisit l'ordre des arrêts.

Le simulateur utilise une grille géographique fictive, pas Haversine :

```text
nord = abs(latitude_arrivée - latitude_départ) × 111.32
axe_est = abs(longitude_arrivée - longitude_départ) × 111.32 × cos(latitude_moyenne)
km_segment = arrondi_2_décimales((nord + axe_est) × 1.15)
minutes_segment = arrondi_2_décimales(3 × km_segment + 2) si km_segment > 0, sinon 0
```

Ces constantes décrivent uniquement un réseau de démonstration. Ce ne sont pas des
mesures routières réelles ou une estimation de trafic. Les tests peuvent injecter
une matrice dirigée de distances/durées à la place de cette grille.
Haversine reste disponible pour les champs historiques `agencies[].distance_km` et
`stops[].distance_km`, qui sont **à vol d'oiseau**. Seuls `total_distance_km` et les
segments de `route` décrivent la tournée routière simulée.

L'ordre minimise **d'abord la durée totale**, puis la distance en cas d'égalité,
puis l'ordre des identifiants pour conserver un résultat déterministe. Toutes les
permutations sont évaluées jusqu'à trois arrêts ; au-delà, un algorithme exact
Held-Karp travaille sur la matrice des segments. Les durées/distances de ce MVP
ont une précision au centième. Un cache de segments et de tournées est limité à la
comparaison en cours. Pas de cache persistant des positions.

## Règles du comparateur

Les règles de stock et de conditionnement V0.1 sont conservées : 1 à 100 lignes,
produits existants, quantités positives et regroupées par produit, références actives,
`packs = ceil(quantité / reference_quantity)`, stock suffisant dans **une même agence**
pour toute une ligne. Une ligne n'est pas fractionnée entre des offres. Tous les
prix des matériaux sont des `Decimal`, arrondis au centime `ROUND_HALF_UP`.

L'optimiseur explore les sous-ensembles des agences éligibles et, pour chacun,
les seuils de préparation présents dans les offres. Il choisit les lignes les moins
chères respectant le seuil, puis calcule la tournée des agences effectivement
utilisées. Les seuils permettent de retenir une référence légèrement plus chère
qui sera prête beaucoup plus tôt. Il ne se contente pas de comparer les anciens
paniers mono-fournisseurs à un unique panier de prix minimum.

Les trois stratégies ont des règles de sélection explicites :

1. **1 seul arrêt** : coût d'approvisionnement minimal parmi les solutions complètes
   dans **une seule agence**, qui n'est pas nécessairement l'enseigne la moins chère.
2. **Prix matériaux minimum** : prix d'achat des matériaux minimal sur toutes les
   agences ; départage des égalités par coût d'approvisionnement.
3. **Meilleur compromis** : coût d'approvisionnement minimal sur les solutions
   complètes calculées avec la règle d'ordre de visite ci-dessus.

À égalité de coût : matériaux, nombre d'arrêts, durée de trajet, puis identifiants
des agences. Une même enseigne peut compter plusieurs arrêts. Une absence de
solution complète produit `valid=false` et des coûts `null`, jamais un panier
partiel classé comme meilleure solution. L'API conserve les anciens sous-totaux
partiels dans `options` et l'interface les affiche dans le volet V0.1.

La recherche est bornée par `OPTIMIZER_MAX_AGENCIES` (8 par défaut, 6 dans le jeu de
données). Un dépassement renvoie 422 avec un message explicite : aucune agence
n'est ignorée silencieusement. Pour élargir le périmètre au-delà du MVP, remplacer
la stratégie de recherche plutôt que supprimer cette limite.

### Formule exacte du meilleur compromis

Variables :

- `M` : total réel des matériaux HT, conditionnements compris ;
- `D` : distance de la tournée aller-retour, en km ;
- `T` : durée de trajet de cette tournée, en minutes ;
- `P` : délai maximum de préparation des offres choisies, en minutes ;
- `N` : nombre d'agences visitées ;
- `K` : `COST_PER_KM`, **0,50 €/km** par défaut ;
- `V` : `TIME_VALUE_PER_HOUR`, **30 €/h** par défaut ;
- `A` : `EXTRA_STOP_COST`, **5 €** par arrêt supplémentaire par défaut.

```text
coût_distance = arrondi_centime(D × K)
coût_temps = arrondi_centime((T + P) × V / 60)
pénalité_arrêts = arrondi_centime(max(N - 1, 0) × A)
coût_estimé_approvisionnement = M + coût_distance + coût_temps + pénalité_arrêts
```

Chaque composante monétaire est arrondie **séparément**, avec `Decimal` et
`ROUND_HALF_UP`, avant addition. Les valeurs géographiques sont converties en
`Decimal` depuis leur représentation textuelle, jamais via `Decimal(float)`.

**Hypothèse de temps explicite** : les agences préparent en parallèle ; l'artisan
attend `P` avant de partir, puis effectue la tournée complète. Donc
`total_minutes = P + T`. Il n'y a ni chevauchement préparation/trajet, ni simulation
d'arrivée dans une agence avant qu'elle soit prête. C'est une hypothèse conservatrice
à ajuster ultérieurement selon les usages réels. La pénalité d'arrêt est un indicateur
fixe de gêne/manutention, pas une durée ajoutée.

Le prix des matériaux reste visible séparément du **coût estimé non facturé**.
Ce coût sert uniquement à comparer les solutions ; il ne crée aucune facturation.

### Exemple vérifié

Origine `10 rue du Chantier, 75004 Paris`, panier : 30 BA13, 10 rails, 20 montants.
Paramètres par défaut, données de seed inchangées :

| Stratégie | Matériaux HT | Arrêts | Distance A/R | Trajet | Préparation max. | Coût estimé non facturé |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 seul arrêt | 380,20 € | 1 | 13,80 km | 45,40 min | 150 min | 484,80 € |
| Prix matériaux minimum | 375,40 € | 2 | 15,33 km | 51,99 min | 150 min | 489,07 € |
| Meilleur compromis | 377,40 € | 3 | 21,48 km | 72,44 min | 90 min | 479,36 € |

Le prix minimum économise **4,80 € de matériaux** contre l'arrêt unique, mais ajoute
**6,59 minutes de trajet** et **1,53 km**. Le compromis retient trois arrêts car la
préparation maximale descend à 90 minutes. Son temps total est **162,44 min**, contre
195,40 min pour un seul arrêt ; son coût calculé est :

```text
377,40 + (21,48 × 0,50) + ((72,44 + 90) × 30 / 60) + (2 × 5)
= 377,40 + 10,74 + 81,22 + 10,00
= 479,36 €
```

Ordre du compromis : Chantier → GEDIMAT TEST Paris Est → POINT.P TEST Paris Est →
GEDIMAT TEST Ivry → Chantier. L'interface détaille chaque segment, le calcul et les
matériaux affectés à chaque agence dans trois volets dépliables.

## Remplacer les services géographiques ultérieurement

- Implémenter `GeocodingService.geocode(address)` en retournant `Coordinates` et
  définir son identifiant `provider`. Brancher l'instance dans `get_geocoding_service`.
- Implémenter `RoutingService.leg(start, end)` en retournant un `RouteLeg` validé,
  avec distance/durée par segment dirigé, normalisées au centième. Définir `provider`
  et `simulated=False`, puis remplacer `get_routing_service`.
- Garder la résolution d'origine, les connecteurs et le moteur de comparaison
  inchangés. L'API utilise des dépendances FastAPI surchargeables dans les tests.
- Si nécessaire, fournir un autre `RouteOrderOptimizer` au constructeur du
  comparateur. Aucun nom de fournisseur cartographique n'est inscrit dans le moteur.
- Pour un futur service réseau, définir dans l'adaptateur les délais, erreurs,
  limites et règles de fallback explicites. Ne jamais transformer une panne en
  itinéraire réel fictif. Aucun fournisseur réseau ni fallback réseau n'est ajouté ici.

## Connecteurs et extension future

`SupplierConnector` expose :

```python
@property
def supplier_name(self) -> str: ...

def get_offers(self, product_ids: list[int]) -> list[ConnectorOffer]: ...
```

La méthode groupée évite un appel par produit/agence. Le connecteur résout les
correspondances entre identifiants PMC et références fournisseurs. Il renvoie un
format commun : fournisseur, agence, produit PMC, référence, unité, conversion,
prix, stock, quantité disponible en unités PMC, préparation et date de mise à jour.
`available_quantity` est calculé depuis le stock et la conversion pour éviter
les valeurs incohérentes.

Pour ajouter ultérieurement un vrai fournisseur :

1. Créer ses entrées `Supplier`, `Agency` et les mappings `SupplierProduct` validés.
2. Implémenter un nouveau `SupplierConnector` dans `app/connectors/`.
3. Dans cet adaptateur uniquement, traduire les unités, prix HT, stocks par agence
   et dates vers `ConnectorOffer` ; définir des délais réseau et une gestion explicite
   des erreurs. Un fournisseur en erreur ne doit pas être présenté comme en rupture.
4. Injecter ce connecteur à la place du simulateur dans `build_connectors`.
5. Ajouter les tests du contrat normalisé et des tests avec réponses fournisseur
   enregistrées/simulées. Conserver les tests du moteur sans changement.
6. Ajouter la configuration et les éventuels secrets par environnement, jamais dans Git.

Le comparateur construit ses options à partir des connecteurs injectés : il ne
contient aucun nom d'enseigne codé en dur. Les appels réels et leur résilience
ne font pas partie de cette livraison.

## API

| Méthode | Route | Fonction |
| --- | --- | --- |
| GET | `/api/products?q=BA13&limit=100&offset=0` | Catalogue, recherche nom/code/catégorie et pagination |
| GET | `/api/products/{id}` | Fiche normalisée, 404 si absente |
| POST | `/api/compare` | Comparaison validée, 404 produit absent, 422 panier invalide |
| GET | `/api/health` | Vérification de la connexion à la base |

```bash
curl -X POST http://localhost:8000/api/compare \
  -H 'Content-Type: application/json' \
  -d '{"lines":[{"product_id":1,"quantity":"30"},{"product_id":2,"quantity":"10"},{"product_id":3,"quantity":"20"}],"origin":{"type":"site","address":"10 rue du Chantier, 75004 Paris"}}'
```

Formats d'origine :

```json
{"type": "site", "address": "10 rue du Chantier, 75004 Paris"}
{"type": "current_location", "latitude": 48.8566, "longitude": 2.3522}
{"type": "company"}
{"type": "other", "address": "5 rue des Artisans, 94200 Ivry-sur-Seine"}
```

`site` et `other` acceptent aussi une paire de coordonnées à la place de l'adresse.
Il faut choisir adresse **ou** coordonnées ; latitude/longitude doivent être fournies
ensemble, finies et dans leurs bornes. `company` utilise uniquement la configuration.
`origin` absent ou nul conserve le comportement V0.1 avec `USER_LATITUDE/LONGITUDE`.

La réponse ajoute `origin` résolue, `cost_parameters`, `strategies` et
`minimum_vs_single` (écarts signés, nul si aucune solution à un arrêt).
Chaque stratégie contient `material_total`, `estimated_procurement_cost`, `stops`
(liste **ordonnée**, donc `len(stops)` = nombre d'arrêts), `supplier_count`,
`total_distance_km`, `travel_minutes`, `max_preparation_minutes`, `total_minutes`,
`route` (points, segments, fournisseur simulé, méthode d'ordre), `lines`,
`cost_breakdown`, `unavailable`, `valid` et `explanation`.
Les montants et totaux décimaux restent des chaînes JSON.
`options` conserve intégralement les trois comparaisons par fournisseur V0.1.

Les identifiants ci-dessus correspondent à une base neuve. Consulter le catalogue
si les données ont été modifiées. Les erreurs SQL renvoient un message 503 générique
sans exposer les requêtes, paramètres ou identifiants de connexion.

## Développement et tests

Python 3.12 minimum. L'environnement de développement est séparé du runtime Docker.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check .
ruff format --check .
```

Par défaut, pytest utilise une base SQLite temporaire par test, avec clés étrangères
activées ; les tests du service pur n'ont pas besoin de base. Pour un PostgreSQL
dédié aux tests :

```bash
TEST_DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/promatconnect_test' pytest -q
```

**La base de test doit exister et être jetable** : les fixtures recréent les tables.
Le nom doit finir par `_test` ; ne jamais utiliser la base applicative.

Pour exécuter la suite sur PostgreSQL via Docker :

```bash
docker compose up -d db
# Création unique de la base réservée aux tests :
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" promatconnect_test'
docker compose --profile test run --build --rm tests
```

Pour exécuter Python hors Docker, fournir l'URL d'une base PostgreSQL déjà créée
et accessible depuis l'hôte (le service Compose n'expose pas de port PostgreSQL) :

```bash
export DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/promatconnect'
python -m scripts.seed
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Couverture fonctionnelle : catalogue, seed idempotent, contraintes de données,
mappings actifs, connecteurs, décimaux, conversions, stocks limites, arrondis de
conditionnements, comparaison mono/multi, ruptures, distances, validation API,
réponses d'erreur, page HTML, fichiers statiques et Swagger. V0.2 ajoute les origines,
le géocodage simulé, les boucles dirigées, les permutations, Held-Karp, la formule
financière, les seuils de préparation, la sélection d'un vrai arrêt unique et le
contrôle du compromis contre une énumération indépendante des affectations.

Le parcours navigateur optionnel `tests/browser_smoke.cjs` utilise Playwright,
uniquement comme outil de vérification hors de l'application :

```bash
npm install --prefix /tmp/promatconnect-browser playwright@1.58.2
/tmp/promatconnect-browser/node_modules/.bin/playwright install chromium
NODE_PATH=/tmp/promatconnect-browser/node_modules node tests/browser_smoke.cjs
```

Chromium nécessite ses bibliothèques système usuelles. `APP_URL` permet de choisir
une autre URL locale. Le script vérifie ordinateur/mobile, les quatre origines,
le refus GPS, l'absence de géolocalisation automatique, une réponse GPS tardive,
les résultats, les détails et les ruptures. Ses captures sont écrites dans le dossier
temporaire du système, sans ajouter d'images au dépôt.

## État de validation de la livraison

Voir `VALIDATION.md` pour les résultats réellement obtenus et les limites de
l'environnement de vérification. La validation Compose ne remplace pas un build
réussi ni un lancement des conteneurs.

## Limites assumées du MVP

- Données locales fictives, sans actualisation distante ni réservation de stock.
- Panier en mémoire dans le navigateur : un rechargement le remet à zéro.
- Interface chargée avec les 20 produits de démonstration ; pour un grand catalogue,
  brancher la recherche/pagination serveur déjà exposée par l'API.
- Création de schéma par `create_all`, sans migration de schéma existant. Prévoir
  des migrations avant une évolution du modèle sur des données conservées.
- Démonstration locale sans authentification ; aucun déploiement public inclus.
- Pas de calcul TVA, de facturation des déplacements, d'horaires d'ouverture,
  de navigation réelle, de trafic, de fractionnement d'une ligne ni de mode Express.
- Géocodage limité aux adresses fictives ; routage en grille simulée, pas une carte routière.
- Préparation maximale entièrement attendue avant le départ, sans chevauchement du trajet.
- Exploration bornée à 8 agences par défaut ; à élargir via une stratégie adaptée avant
  le raccordement à un vaste réseau réel.
