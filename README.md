# ProMatConnect — MVP technique 0.1

Comparateur B2B de matériaux pour les artisans du bâtiment. L'application permet de
constituer une liste, de modifier les quantités et de comparer trois options :
**tout chez POINT.P TEST**, **tout chez GEDIMAT TEST** et **panier optimisé**.

Toutes les agences, références, disponibilités et offres sont **simulées**.
Aucun scraping, appel d'API fournisseur, compte utilisateur, paiement, commande,
facturation, chantier ou livraison n'est intégré. Les prix sont en **EUR HT**.

## Démarrage avec Docker

Prérequis : Docker Engine / Docker Desktop démarré et Docker Compose v2.
Sous WSL, activer l'intégration de la distribution dans Docker Desktop.

```bash
cd /home/pi/Batiplus/App
cp .env.example .env
docker compose up --build
```

Le chemin ci-dessus correspond au dossier de cette livraison ; adapter si déplacé.
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
| `USER_LATITUDE` | `48.8566` | Position fictive, de -90 à 90 |
| `USER_LONGITUDE` | `2.3522` | Position fictive, de -180 à 180 |
| `SEED_ON_START` | `true` | Insérer les données de démonstration manquantes |
| `DATABASE_URL` | URL PostgreSQL locale | Connexion SQLAlchemy hors Docker |

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
│   │   └── comparison.py       # Panier et résultats typés
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
│   │   ├── comparison.py       # Algorithme indépendant de SQL et des enseignes
│   │   └── distance.py         # Haversine
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
│   └── test_api.py
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

## Règles du comparateur

1. Valider 1 à 100 lignes ; rejeter les produits inconnus et les lignes dupliquées.
   L'interface additionne les quantités lorsqu'un produit est ajouté à nouveau.
2. Récupérer les offres actives des connecteurs pour les produits demandés.
3. Calculer `packs = ceil(quantité demandée / reference_quantity)`.
4. Une offre est éligible si son stock couvre ces conditionnements entiers.
5. Comparer le **coût réellement acheté**, `packs × price`, avec arrondi au centime
   `ROUND_HALF_UP`, et non uniquement le prix à l'unité de référence.
6. Pour chaque option mono-fournisseur, choisir l'offre éligible la moins chère de
   chaque ligne dans les agences de cette enseigne.
7. Pour le panier optimisé, appliquer la même sélection sur l'ensemble des fournisseurs.
8. À prix égal, départager par distance, délai, enseigne, agence et référence.

Une ligne n'est jamais fractionnée entre deux offres/agences. Les stocks d'agences
ne sont pas additionnés. Une option mono-fournisseur peut nécessiter plusieurs
agences. L'algorithme minimise les matériaux achetés, pas les trajets ou le nombre
d'arrêts ; il ne réserve aucun stock.

Pour chaque option : produits couverts/manquants, lignes détaillées avec surplus,
références et dates, fournisseurs distincts, agences distinctes, distance de chaque
agence depuis la position fictive et délai de préparation maximum.
Les distances utilisent Haversine (rayon terrestre 6 371 km). Elles ne représentent
ni un trajet routier, ni une tournée cumulée.

Une option incomplète renvoie `valid=false`, `total=null` et `available_subtotal`
correspondant uniquement aux lignes couvertes. L'interface affiche **Panier incomplet**,
jamais un sous-total comme s'il représentait le panier entier.
Le délai maximum concerne les lignes disponibles, sans temps de déplacement.

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
  -d '{"lines":[{"product_id":1,"quantity":"30"},{"product_id":2,"quantity":"10"},{"product_id":3,"quantity":"20"}]}'
```

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
réponses d'erreur, page HTML, fichiers statiques et Swagger.

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
- Pas de calcul TVA, de frais de trajet, de distance routière, d'horaires d'ouverture,
  de fractionnement d'une ligne ni d'optimisation du nombre d'arrêts.
