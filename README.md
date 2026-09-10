# Veille marchés publics — architecture (Belgique + Europe)

Application de veille quotidienne automatisée : elle interroge les plateformes de
marchés publics, filtre les avis liés à l'architecture (codes CPV + mots-clés
FR/NL/EN), déduplique entre sources, historise dans SQLite et produit un digest
quotidien consultable (web, CSV, HTML, Markdown, email).

---

## 1. Démarrage rapide

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

veille init                 # crée config/config.yaml
veille test -s sample       # veille ponctuelle hors ligne : valide le filtrage
veille doctor               # vérifie l'accès réel à chaque source
veille run                  # veille complète : collecte + base + digest
veille web                  # interface de consultation sur http://127.0.0.1:8080
```

`veille test` ne touche pas la base. Ajoutez `--persist` pour enregistrer.

---

## 2. Stack technique

| Brique | Choix | Pourquoi |
|---|---|---|
| Langage | Python 3.10+ | écosystème HTTP/parsing, déploiement simple |
| Base | SQLite (`sqlite3` stdlib) | zéro serveur, fichier unique, suffisant pour ~10⁵ avis |
| HTTP | `requests` + throttling maison | limite de débit par domaine, backoff, `Retry-After` |
| Parsing flux | `feedparser` | tolérant aux RSS/Atom malformés |
| Interface | Flask + Jinja2 | une page de liste filtrable, sans build front |
| Planification | cron / systemd, ou `veille daemon` | cron recommandé en production |
| Tests | pytest (53 tests, sans réseau) | les APIs externes sont simulées |

Aucune dépendance lourde, pas de conteneur requis, pas de clé d'API.

---

## 3. État réel de l'accès aux données

**À lire avant d'activer les sources belges.**

| Source | Accès | Statut |
|---|---|---|
| **TED** (Office des publications de l'UE) | API de recherche officielle, anonyme, sans clé | Connecteur prêt, **activé par défaut** |
| **e-Notification / e-Procurement** (SPF BOSA) | Pas d'API publique documentée à ce jour | Connecteur `http_search` **désactivé par défaut**, à configurer après vérification |
| **Bulletin des Adjudications** | Flux RSS selon les recherches sauvegardées | Connecteur `rss` **désactivé par défaut**, à alimenter avec vos URLs de flux |

L'environnement de développement utilisé pour écrire ce code n'avait **pas** d'accès
réseau sortant vers `ted.europa.eu` ni `publicprocurement.be` (politique de proxy).
Les connecteurs n'ont donc **pas** pu être validés contre les serveurs en production :
ils sont couverts par des tests qui simulent les réponses, et la vérification
terrain se fait chez vous avec :

```bash
veille doctor
```

Cette commande, pour chaque source : teste les endpoints, lit le `robots.txt`,
et affiche ce qui répond réellement. **Lancez-la en premier.** Voir
[`docs/SOURCES.md`](docs/SOURCES.md) pour la marche à suivre détaillée
et les vérifications juridiques préalables.

### Déontologie

- Les API officielles sont privilégiées partout où elles existent.
- Le connecteur `http_search` **refuse de démarrer** si le `robots.txt` interdit
  le chemin visé, et applique le `Crawl-delay` déclaré.
- Le débit est limité (1 req/s par défaut, `http.requests_per_second`).
- Le `User-Agent` est explicite : renseignez-y une adresse de contact.
- Vérifiez les conditions d'utilisation de chaque plateforme avant activation.

---

## 4. Fonctionnement

```
sources ─┬─ ted (API officielle)          ┐
         ├─ rss (flux configurables)      ├─→ normalisation (modèle Notice)
         ├─ http_search (config-driven)   │        │
         └─ sample (fixtures hors ligne)  ┘        ▼
                                            filtrage CPV + mots-clés (score 0..1)
                                                   │
                                                   ▼
                                     déduplication inter-sources
                                                   │
                                                   ▼
                          SQLite (historique, doublons, journal des runs)
                                                   │
                        ┌──────────────────────────┼──────────────────────┐
                        ▼                          ▼                      ▼
                 interface web              digest HTML/CSV/MD        email SMTP
```

### Filtrage

Score entre 0 et 1, seuil configurable (`filtering.min_score`, défaut 0.5) :

| Signal | Poids par défaut |
|---|---|
| CPV exactement dans la liste | 1.0 — l'avis est **toujours** conservé |
| CPV dans une famille surveillée (`712…`) | 0.6 |
| Mot-clé dans le titre | 0.45 (+0.1 par mot-clé supplémentaire) |
| Mot-clé dans la description | 0.2 |
| Mot-clé d'exclusion | rejet immédiat |

La comparaison est insensible à la casse et aux accents, sur des limites de mots
(« architecte » ne matche pas « départ »). Le double filet CPV + mots-clés est
volontaire : les avis belges sont souvent mal codés côté CPV.

Les mots-clés d'exclusion éliminent le bruit courant (« architecture logicielle »,
« IT-architectuur »…).

### Déduplication

Un même marché publié sur TED et sur e-Notification est regroupé :

1. empreinte `acheteur + titre + date limite` (normalisés) ;
2. rapprochement avec l'historique déjà en base ;
3. rapprochement flou sur le titre (ratio ≥ 0.92) au sein d'un même acheteur.

L'avis de la source la plus prioritaire (`dedup.source_priority`) reste canonique ;
les autres sont conservés en base mais marqués `duplicate_of` et masqués des listes.

### Nouveautés

Chaque avis porte `first_seen_at` et `run_id`. Le digest ne liste que ce qui est
apparu pendant le run (`notify.new_only`). Un deuxième run le même jour signale
donc 0 nouveauté.

### Résilience

Une source qui échoue est isolée : l'erreur est journalisée en base
(table `source_runs`, visible sur `/runs`), le run continue avec les autres
sources, et rien de ce qui a déjà été collecté n'est perdu. La fenêtre glissante
(`lookback_days`, défaut 7 jours) rattrape automatiquement les journées manquées.

---

## 5. Commandes

| Commande | Rôle |
|---|---|
| `veille init` | crée `config/config.yaml` depuis l'exemple |
| `veille doctor` | teste l'accès à chaque source (endpoints, robots.txt) |
| `veille test` | **mode test** : veille ponctuelle immédiate, sans écriture |
| `veille run` | veille complète : collecte, stockage, digest, email |
| `veille list` | liste les avis stockés (filtres, `--json`) |
| `veille stats` | statistiques et derniers runs, en JSON |
| `veille web` | interface de consultation |
| `veille daemon` | boucle quotidienne interne (alternative à cron) |

Quelques exemples :

```bash
veille test -s ted --lookback 3 -n 10       # 3 derniers jours, 10 avis affichés
veille test -s sample --json /tmp/out.json  # valide le filtrage hors ligne
veille list --open-only --order deadline    # ce qui reste à déposer, par urgence
veille list --country BE --cpv 71200000
veille run -s ted --mode manual
```

---

## 6. Planification quotidienne

**cron (recommandé)** :

```cron
0 7 * * * /chemin/vers/March-spublics/scripts/run-daily.sh >> /chemin/vers/March-spublics/data/logs/cron.log 2>&1
```

**systemd** : `scripts/veille.timer` + `scripts/veille-run.service` (run quotidien),
ou `scripts/veille.service` (mode daemon persistant). L'interface web a son unité
dédiée : `scripts/veille-web.service`.

**Sans planificateur système** : `veille daemon --at 07:00 --run-now`.

---

## 7. Configuration

Tout est dans `config/config.yaml` (`config/config.example.yaml` sert de valeurs
par défaut et de documentation). Les secrets passent par variables
d'environnement :

```bash
export VEILLE_NOTIFY__EMAIL__PASSWORD='...'
export VEILLE_FILTERING__MIN_SCORE=0.6
```

Convention : `VEILLE_` + chemin en majuscules, niveaux séparés par `__`.

### Élargir la veille à un autre domaine

Modifiez uniquement `filtering.cpv_codes`, `filtering.cpv_prefixes` et
`filtering.keywords`. Aucun code à toucher.

### Email quotidien

```yaml
notify:
  email:
    enabled: true
    smtp_host: smtp.gmail.com
    smtp_port: 587
    username: vous@example.com
    sender: vous@example.com
    recipients: ["vous@example.com"]
```

Le digest part en HTML + texte, avec le CSV en pièce jointe. Rien n'est envoyé
s'il n'y a aucune nouveauté (`skip_if_empty`).

---

## 8. Ajouter une source

L'architecture est modulaire : un connecteur = une classe.

1. Créez `src/veille_mp/connectors/ma_source.py` :

```python
from .base import Connector
from ..models import Notice

class MaSourceConnector(Connector):
    type_name = "ma_source"          # valeur du champ "type" en config

    def fetch(self) -> list[Notice]:
        response = self.http.get(self.spec["url"])   # throttling automatique
        return [Notice(source=self.name, source_id=..., title=..., ...)]

    def check(self) -> tuple[bool, str]:             # utilisé par `veille doctor`
        return True, "OK"
```

2. Enregistrez-la dans `src/veille_mp/connectors/__init__.py`.
3. Ajoutez un bloc dans `sources:` avec `type: ma_source`.

Le filtrage, la déduplication, le stockage et le digest s'appliquent
automatiquement. Avant d'écrire du code : `type: rss` et `type: http_search`
couvrent déjà beaucoup de cas sans programmation.

---

## 9. Tests

```bash
pytest -q          # 53 tests, aucun accès réseau
```

Les tests couvrent : normalisation multilingue eForms, les deux dialectes de
l'API TED (avec bascule d'endpoint), le filtrage CPV/mots-clés, la déduplication
inter-sources, l'historique, l'isolation des erreurs de source, le blocage par
`robots.txt`, et la génération des digests.

---

## 10. Structure

```
src/veille_mp/
├── cli.py              commandes
├── config.py           YAML + surcharges d'environnement
├── models.py           modèle Notice normalisé
├── db.py               SQLite (avis, doublons, runs)
├── filtering.py        score CPV + mots-clés
├── dedup.py            déduplication inter-sources
├── pipeline.py         orchestration, isolation des erreurs
├── http.py             client throttlé + retries
├── robots.py           garde robots.txt
├── textutil.py         normalisation texte / dates / montants
├── connectors/         ted, rss, http_search, sample
├── notify/             digest HTML/CSV/MD + email SMTP
└── web/                interface Flask
```

---

## 11. Limites connues

- Le connecteur belge `http_search` demande une configuration manuelle de
  l'endpoint : aucune API publique documentée n'a pu être confirmée.
- La déduplication floue suppose un nom d'acheteur écrit de la même façon d'une
  source à l'autre ; les variantes fortes (« Ville de Namur » / « Namur, Ville de »)
  passent par l'empreinte titre + échéance seulement.
- Le budget estimé est absent de beaucoup d'avis : la colonne reste vide.
- SQLite suffit ici ; passer à PostgreSQL demanderait de réécrire `db.py`.
