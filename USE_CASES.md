# Cas d'usage et enchaînements

Ce guide montre comment employer les cinq endpoints avec les paramètres que
le service honore réellement. La référence des champs est `README.md` ; les
modèles Pydantic de `app/api/models.py` et
`app/api/v1/endpoints/research_deep.py` font foi. Tous les exemples
supposent `X-API-Key` et l'API sur `http://localhost:8000`.

```bash
export API=http://localhost:8000/api/v1
export H='X-API-Key: '"$WEBTOOLS_API_KEY"
```

## Quel endpoint choisir

| Besoin | Endpoint | Appel LLM | Durée |
|---|---|---|---|
| Lire une page connue | `/extract` | selon les paliers | de quelques secondes à plus d'une minute avec l'agent |
| Décrire ou lire une image | `/vision` | oui | secondes |
| Trouver des pages | `/search` | non | la plus courte |
| Question factuelle courte | `/research/quick` | oui, un seul | de quelques secondes à une minute |
| Rapport structuré avec bibliographie | `/research/deep` | oui, nombreux | plusieurs minutes (4 à 7 par tâche sur le benchmark) |

Commencer par le plus léger qui suffit. Un rapport approfondi demande de
nombreux appels LLM et plusieurs minutes.

## 1. Un endpoint seul

### Extraction

Contenu d'une page, extraction par défaut (RSS, navigateur, LLM léger, agent) :

```bash
curl -X POST "$API/extract" -H "$H" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article", "extraction_type": "article"}'
```

Avec une consigne : `"prompt": "Liste les décisions et leurs dates"`. La
consigne complète le gabarit d'extraction, elle ne le remplace pas.

Sans agent navigateur (page statique, plus rapide, sans appel LLM) :

```json
{"url": "https://example.com", "options": {"use_agent": false, "timeout": 30}}
```

Avec un LLM propre à la requête : `llm_config` (`provider`, `api_key`,
`model`, `base_url`). Son `base_url` est refusé s'il vise une adresse non
publique.

La réponse porte `content`, `title`, `content_type` et `metadata`. Un échec
rend `success: false` et `error`, avec le code HTTP 200 : lire `success`.

### Vision

```bash
curl -X POST "$API/vision" -H "$H" -H "Content-Type: application/json" \
  -d '{"image_url": "https://example.com/graphique.png", "prompt": "Extrais les valeurs du graphique sous forme de tableau"}'
```

Selon la consigne : lecture du texte, description, lecture d'un graphique.
Le modèle configuré doit gérer la vision (`GET /api/v1/vision/health` le
vérifie sans appeler le modèle). Pour une analyse factuelle, laisser
`temperature` à 0,15.

### Recherche

```bash
curl -X POST "$API/search" -H "$H" -H "Content-Type: application/json" \
  -d '{"query": "directive européenne marchés publics 2024", "max_results": 10, "language": "fr"}'
```

`categories` et `engines` sont transmis à SearXNG (`"engines": "wikipedia"`,
`"categories": "news"`). `language` vaut `fr` par défaut : pour des sources
anglaises, passer `"language": "en"`. Les opérateurs de recherche des moteurs
(`site:`, `filetype:pdf`) s'écrivent dans `query` ; leur effet dépend du
moteur interrogé.

### Réponse rapide

```bash
curl -X POST "$API/research/quick" -H "$H" -H "Content-Type: application/json" \
  -d '{"query": "Quelles sont les trois plus grandes villes de France ?", "max_sources": 5}'
```

Lire `sources_attempted`, `sources_extracted` et `discarded` : une réponse
bâtie sur une seule source extraite sur cinq tentées n'a pas le même poids
qu'une réponse bâtie sur cinq. `confidence` est toujours `null`. `/research/quick`
ne prend pas de liste d'URL : pour imposer des sources, utiliser
`/research/deep` avec `sources.required`.

### Rapport approfondi

Sujet libre :

```bash
curl -X POST "$API/research/deep" -H "$H" -H "Content-Type: application/json" \
  -d '{"topic": "Impact de l'\''intelligence artificielle sur l'\''emploi en France", "timeout": 900}'
```

Structure imposée (le rapport contient exactement ces sections, dans cet ordre) :

```json
{
  "topic": "Réglementation européenne des marchés publics numériques",
  "output_format": {"sections": ["Cadre juridique", "Procédures", "Évolutions récentes"]}
}
```

Sources obligatoires et domaines :

```json
{
  "topic": "Évolution de la population des communes de plus de 100 000 habitants",
  "sources": {
    "required": ["https://www.insee.fr/fr/statistiques/1405599"],
    "domains_whitelist": ["insee.fr", "data.gouv.fr"],
    "exclusions": ["forum.example.org"]
  },
  "max_sources_per_section": 6
}
```

- `sources.required` : chaque URL est extraite avant la rédaction, même hors
  de `domains_whitelist`, et proposée à toutes les sections. Si une URL ne
  peut pas être extraite, le rapport est `degraded` et la réponse liste
  `required_sources_failed`. Une URL extraite mais non citée figure dans
  `required_sources_uncited`.
- `domains_whitelist` et `exclusions` filtrent les résultats de recherche,
  pas les sources requises.
- Il n'existe pas de mode « uniquement ces URL » : les moteurs de recherche
  restent interrogés. Pour s'en rapprocher, combiner `sources.required` et
  `domains_whitelist`.
- `output_format.include_section_data: true` joint à chaque section les
  passages extraits (texte intégral, réponse volumineuse).

Lire la réponse d'un rapport approfondi :

| Champ | À regarder |
|---|---|
| `success`, `degraded`, `timed_out` | `success` est faux pour un rapport dégradé ; `result` est alors présent s'il existe |
| `failed_sections` | Sections dont la rédaction a échoué 3 fois (incident LLM, à relancer) |
| `unsourced_sections` | Sections retirées faute de matière exploitable (constat sur le sujet, inutile de relancer à l'identique) |
| `missing_sections` | Sections non rédigées quand le délai global a expiré |
| `diagnostics.invariants` | Citation orpheline, marqueur interne, section tronquée |
| `diagnostics.events` | Replis rencontrés (plan générique, recherche en échec...) |
| `result.bibliography` | Les `[n]` du texte renvoient à ces entrées |

## 2. Enchaînements

### Rechercher, puis extraire

`/search` donne des URL, `/extract` lit celles qui comptent. Chaque
extraction est indépendante : les lancer en parallèle (quelques-unes à la
fois, chaque extraction peut ouvrir un navigateur).

```python
import asyncio, httpx

async def lire(client, url):
    r = await client.post("/extract", json={"url": url, "options": {"timeout": 45}})
    return r.json()

async def chercher_et_lire(client, query):
    hits = (await client.post("/search", json={"query": query, "max_results": 5})).json()
    pages = await asyncio.gather(*(lire(client, h["url"]) for h in hits["results"][:3]))
    return [p for p in pages if p.get("success")]
```

### Image, puis recherche

Lire le texte d'une capture avec `/vision`, puis chercher les termes
trouvés avec `/search` ou poser la question à `/research/quick`.

### Escalade : rapide, puis approfondi

Poser d'abord la question à `/research/quick`. Passer à `/research/deep` si
`sources_extracted` est faible, si la réponse est incomplète ou si la
question demande plusieurs aspects.

```python
quick = (await client.post("/research/quick", json={"query": q})).json()
if not quick["success"] or quick["sources_extracted"] < 2:
    deep = (await client.post("/research/deep", json={"topic": q, "timeout": 900})).json()
```

### Via le serveur MCP

Le serveur MCP expose les mêmes capacités à un assistant. Pour un rapport
approfondi, préférer `webtools_research_deep_start`, qui rend un identifiant
de tâche immédiatement, puis interroger `webtools_research_deep_status` :
un appel bloquant de plusieurs minutes peut dépasser le délai du client MCP.

## 3. Cas métier

- **Veille réglementaire** : `/research/deep` avec `domains_whitelist`
  (sites officiels), `output_format.sections` fixes d'une édition à l'autre, et
  `sources.required` pour les textes de référence. Comparer deux éditions
  section par section.
- **Données publiques** : `/search` avec `site:data.gouv.fr` dans la requête,
  puis `/extract` sur les pages retenues.
- **Dossier documentaire** : `/research/deep` avec `include_section_data`,
  pour garder les passages qui fondent chaque section.
- **Lecture de documents visuels** : `/vision` pour les captures et graphiques,
  `/extract` pour les pages qui les contiennent.

## 4. Pour les développeurs d'agents

- **Timeouts** : `/extract` accepte `options.timeout` (5 à 300 s) ;
  `/research/deep` accepte `timeout` (60 à 1200 s, défaut 600). Passé le
  délai, le rapport est partiel (`timed_out`), pas perdu. Régler le délai du
  client HTTP au-dessus de `timeout`.
- **Dégradation** : traiter `degraded` comme un résultat à nuancer, pas comme
  un échec ni comme un succès. Lire `failed_sections` et `unsourced_sections`
  avant de relancer.
- **Charge** : le service plafonne lui-même la concurrence interne
  (`MAX_CONCURRENT_EXTRACTIONS`). Limiter à quelques rapports approfondis
  simultanés ; le serveur MCP en accepte 2 par défaut (`MCP_MAX_CONCURRENT_DEEP`).
- **Cache** : le service garde un cache d'extraction (`CACHE_TTL_SECONDS`). Un
  cache côté client, par URL, évite de rappeler `/extract` pour la même page.
- **Reproductibilité** : la réponse de `/research/deep` contient la
  configuration effective (`diagnostics.config`). La conserver avec le rapport.
