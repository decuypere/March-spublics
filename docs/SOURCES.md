# Sources de données : état, vérification, activation

Ce document explique **comment vérifier** l'accès réel à chaque plateforme avant
de l'activer, et ce qui a pu (ou non) être confirmé au moment de l'écriture du code.

---

## Avertissement sur la vérification

Le code a été écrit dans un environnement dont le proxy sortant bloquait
`ted.europa.eu`, `api.ted.europa.eu` et `www.publicprocurement.be`. Les
connecteurs n'ont donc **pas** pu être testés contre les serveurs de production.

Conséquences pratiques :

- les connecteurs sont **tolérants** : plusieurs endpoints, plusieurs formats de
  réponse, détection automatique du dialecte ;
- les points fragiles (endpoint belge, noms de champs) sont **en configuration**,
  pas en dur dans le code ;
- la vérification terrain se fait chez vous, avec `veille doctor`.

---

## 1. TED — Tenders Electronic Daily

**Ce qui est établi** : l'Office des publications de l'UE expose une API de
recherche publique destinée aux réutilisateurs, sans authentification ni clé,
qui accepte les *expert queries* construites sur le site TED. Depuis 2024 les
avis sont publiés au format **eForms**, dont la structure diffère des anciens XML.

**Ce que fait le connecteur** :

- il essaie les endpoints de `sources[].endpoints` dans l'ordre et retient le
  premier qui répond ;
- il détecte le dialecte à partir de l'URL et adapte le corps de requête :

  | Dialecte | Corps envoyé | Réponse lue |
  |---|---|---|
  | `…/v3/notices/search` | `{query, fields, page, limit, scope}` | `notices` / `totalNoticeCount` |
  | `…/v3.0/notices/search` | `{q, fields, pageNum, pageSize, scope}` | `results` / `total` |

- il construit l'expert query depuis vos CPV, pays et fenêtre de dates :
  - moderne : `classification-cpv IN (71200000 …) AND buyer-country IN (BEL) AND publication-date>=20260901`
  - hérité : `PC=[71200000 OR …] AND CY=[BE] AND PD=[20260901 TO 20260908]`
- **la normalisation ne dépend pas des noms de champs exacts** : les valeurs sont
  retrouvées par motif de clé (`title`, `cpv`, `publication-date`, `deadline`…),
  ce qui couvre eForms *et* l'ancien format dans le même code, et absorbe les
  valeurs multilingues (`{"fra": ["…"], "eng": ["…"]}`).

**Vérifier** :

```bash
veille doctor -s ted
```

La commande affiche l'endpoint retenu, le dialecte, et le nombre d'avis sur la
fenêtre. Si aucun endpoint ne répond :

1. consultez la documentation à jour sur `docs.ted.europa.eu` ;
2. corrigez `sources[].endpoints` dans `config/config.yaml` ;
3. si la syntaxe des requêtes a changé, forcez la vôtre avec `expert_query`,
   testée d'abord dans la recherche experte du site TED.

**Réglage utile** : `countries` accepte des codes ISO 3 lettres (`BEL`, `FRA`…) ;
laissez la liste vide pour couvrir toute l'UE.

---

## 2. e-Notification / e-Procurement (SPF BOSA)

**Ce qui est établi** : `publicprocurement.be` est la plateforme fédérale de
publication ; elle propose aux entreprises des **profils de recherche** avec
notification quotidienne par email. Aucune API publique de recherche
programmatique n'y est documentée.

**Ce que fait le connecteur** (`type: http_search`, désactivé par défaut) :
tout est en configuration — endpoint, méthode, corps de requête, pagination,
et mapping des champs JSON via des chemins pointés. Aucune URL n'est codée en dur.

### Avant d'activer : trois vérifications

**1. Conditions d'utilisation.** Lisez les CGU de la plateforme. Si elles
interdisent la collecte automatisée, n'activez pas la source.

**2. robots.txt.**

```bash
curl -s https://www.publicprocurement.be/robots.txt
veille doctor -s eprocurement_be
```

Le connecteur **refuse de démarrer** si le chemin visé est interdit, et applique
le `Crawl-delay` déclaré. En cas de `robots.txt` illisible, il refuse également
(*fail closed*).

**3. Endpoint réel.** Ouvrez la recherche d'avis dans un navigateur, onglet
Réseau des outils de développement, et repérez la requête qui renvoie les
résultats. Notez : l'URL, la méthode, le corps, et la forme de la réponse.
Reportez-les dans `config/config.yaml` :

```yaml
- name: eprocurement_be
  type: http_search
  enabled: true
  country: BE
  base_url: "https://www.publicprocurement.be"
  method: POST
  search_path: "/le/chemin/observe"
  request:
    json:
      size: 100
      cpv: "{cpv_csv}"
      publishedFrom: "{date_from}"
  pagination: { page_param: "page", start_page: 0, max_pages: 10 }
  mapping:
    items: "data.items"          # chemin vers la liste de résultats
    source_id: "id"
    title: "title"
    buyer_name: "buyer.name"
    url: "url"
    cpv_codes: "cpv[]"           # [] aplatit une liste
    publication_date: "publicationDate"
    deadline: "deadlineDate"
```

Placeholders disponibles dans `request` : `{cpv_csv}`, `{cpv_space}`,
`{date_from}`, `{date_to}`, `{date_from_compact}`, `{date_to_compact}`, `{page}`.

Si la réponse est du HTML et non du JSON, le connecteur le signale explicitement :
il faut alors un connecteur dédié (voir README §8), et l'autorisation de la
plateforme.

### Repli sans scraping

Deux options légitimes si la collecte automatisée n'est pas envisageable :

- **Profil de recherche + email** sur e-Notification, en complément de la veille TED ;
- **agrégateur commercial** (TenderWolf, EBP, TenderAPI…) : API REST unifiée
  Belgique + Europe. Un tel connecteur s'écrit en une classe (README §8) et
  supprime la question du scraping.

Rappel de couverture : TED publie les marchés **au-dessus des seuils européens**.
Les marchés belges sous les seuils n'existent que sur e-Notification — c'est
précisément ce que TED seul ne couvre pas.

---

## 3. Bulletin des Adjudications — flux RSS

**Ce que fait le connecteur** (`type: rss`, désactivé par défaut) : il lit une
liste d'URLs de flux et normalise chaque entrée. Il extrait au passage les codes
CPV (motif à 8 chiffres) et la date limite (motifs FR/NL/EN) depuis le texte.

**Activer** :

```yaml
- name: enot_rss
  type: rss
  enabled: true
  country: BE
  feeds:
    - "https://…/rss?…"
```

Un flux en panne n'empêche pas les autres de fonctionner. `veille doctor -s enot_rss`
indique, flux par flux, le code HTTP et le nombre d'entrées lues.

---

## 4. Codes CPV surveillés

| Code | Libellé |
|---|---|
| 71200000 | Services d'architecture |
| 71210000 | Services de conseil en architecture |
| 71220000 | Services de création architecturale |
| 71221000 | Services d'architecte pour bâtiments |
| 71222000 | Services d'architecte pour espaces extérieurs |
| 71223000 | Services d'architecte pour projets d'agrandissement de bâtiments |
| 71240000 | Services d'architecture, d'ingénierie et de planification |
| 71241000 | Études de faisabilité, service de conseil, analyse |
| 71242000 | Préparation de projet et conception, estimation des coûts |
| 71248000 | Supervision du projet et documentation |
| 71251000 | Services d'architecture et de contrôle des bâtiments |
| 71420000 | Services d'architecture paysagère |

Les familles `712` et `7142` sont surveillées en pertinence partielle : un CPV
voisin non listé (ex. `71230000`, organisation de concours d'architecture)
remonte s'il est accompagné d'un mot-clé pertinent.

---

## 5. Que faire si une source tombe en panne

1. `veille doctor` — identifie la source et le message d'erreur exact.
2. `/runs` dans l'interface web, ou `veille stats` — historique des échecs.
3. `data/logs/veille.log` — trace détaillée.

Une panne d'un jour n'entraîne aucune perte : la fenêtre glissante
`lookback_days` (7 jours par défaut) rattrape les publications manquées au run
suivant, et la déduplication évite les doublons.
