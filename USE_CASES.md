# 📚 WebTools API - Guide des Cas d'Usage et Patterns

> **Guide pratique pour utilisateurs individuels et développeurs d'agents IA**

Ce guide présente les patterns d'utilisation des 13 endpoints WebTools, avec des exemples concrets et des cas d'usage métier. Que vous utilisiez l'API directement ou que vous développiez un agent IA qui orchestrera ces endpoints, vous trouverez ici les bonnes pratiques et patterns optimaux.

---

## 📖 Table des Matières

1. [Patterns Simples (1 endpoint)](#-patterns-simples-1-endpoint)
2. [Patterns de Chaînage (2-3 endpoints)](#-patterns-de-chaînage-2-3-endpoints)
3. [Patterns Avancés (Deep Research)](#-patterns-avancés-deep-research)
4. [Cas d'Usage Métier](#-cas-dusage-métier)
5. [Guide pour Développeurs d'Agents](#-guide-pour-développeurs-dagents)

---

## 🎯 Patterns Simples (1 endpoint)

### 1️⃣ `/api/v1/extract` - Extraction de Contenu

#### Pattern 1.1: Extraction Basique
**Cas d'usage**: Extraire le contenu texte propre d'une page web

```json
POST /api/v1/extract
{
  "url": "https://example.com/article",
  "options": {
    "clean_html": true,
    "extract_images": false,
    "extract_links": false,
    "timeout": 30
  }
}
```

**Résultat**: Texte nettoyé sans navigation, publicités, scripts

---

#### Pattern 1.2: Extraction avec Liens
**Cas d'usage**: Extraire le contenu + tous les liens internes (pour exploration)

```json
POST /api/v1/extract
{
  "url": "https://docs.python.org/3/tutorial",
  "options": {
    "clean_html": true,
    "extract_images": false,
    "extract_links": true,
    "timeout": 30
  }
}
```

**Résultat**: Contenu + liste de liens `[{url, text, type}]`

**Utilisation**:
- Mapper une documentation complète
- Découvrir toutes les sous-pages d'un site
- Base pour un crawler ciblé

---

#### Pattern 1.3: Extraction avec Images
**Cas d'usage**: Récupérer contenu + URLs d'images (graphiques, diagrammes)

```json
POST /api/v1/extract
{
  "url": "https://insee.fr/statistiques-2024",
  "options": {
    "clean_html": true,
    "extract_images": true,
    "extract_links": false,
    "timeout": 45
  }
}
```

**Résultat**: Contenu + URLs images pour analyse ultérieure avec `/vision`

---

### 2️⃣ `/api/v1/vision` - Analyse d'Images

#### Pattern 2.1: OCR (Extraction de Texte)
**Cas d'usage**: Extraire texte depuis une image (document scanné, screenshot)

```json
POST /api/v1/vision
{
  "image_url": "https://example.com/document-scan.png",
  "prompt": "Extraire tout le texte de cette image",
  "temperature": 0.0,
  "max_tokens": 1000
}
```

**Utilisation**:
- Numériser documents papier
- Extraire texte de screenshots
- Récupérer URLs visibles dans captures d'écran

---

#### Pattern 2.2: Analyse de Graphiques/Tableaux
**Cas d'usage**: Extraire les données numériques d'un graphique

```json
POST /api/v1/vision
{
  "image_url": "https://example.com/sales-chart.png",
  "prompt": "Extraire toutes les valeurs numériques et labels de ce graphique. Formater en JSON.",
  "system_prompt": "Tu es un expert en analyse de données visuelles. Retourne uniquement un JSON structuré.",
  "temperature": 0.1,
  "max_tokens": 800
}
```

**Résultat**: Données structurées exploitables (JSON)

---

#### Pattern 2.3: Description/Classification
**Cas d'usage**: Identifier le contenu d'une image (logos, UI, photos)

```json
POST /api/v1/vision
{
  "image_url": "https://example.com/interface.png",
  "prompt": "Décrire cette interface utilisateur. Quels sont les éléments principaux et leur fonction?",
  "temperature": 0.2,
  "max_tokens": 500
}
```

**Utilisation**:
- Audit UX/UI
- Classification automatique d'images
- Détection d'éléments spécifiques

---

### 3️⃣ `/api/v1/search` - Recherche Web Intelligente

#### Pattern 3.1: Recherche Basique
**Cas d'usage**: Recherche simple avec SearXNG

```json
POST /api/v1/search
{
  "query": "FastAPI tutorial",
  "categories": ["general"],
  "language": "fr",
  "max_results": 10
}
```

---

#### Pattern 3.2: Recherche avec Google Dorking
**Cas d'usage**: Recherches spécialisées (fichiers, sites spécifiques, types de contenu)

```json
POST /api/v1/search
{
  "query": "réglementation signalisation routière",
  "dorking": true,
  "categories": ["general", "files"],
  "language": "fr",
  "max_results": 15
}
```

**Comportement**: Active automatiquement les dorks appropriés
- `filetype:pdf` pour documents
- `site:gouv.fr` pour sites officiels
- `intitle:` pour recherches précises

---

#### Pattern 3.3: Ciblage de Site
**Cas d'usage**: Rechercher uniquement sur un site/domaine spécifique

```json
POST /api/v1/search
{
  "query": "API endpoints authentication",
  "target_url": "https://docs.fastapi.com",
  "scope": "site",
  "max_results": 10
}
```

**Options de scope**:
- `"site"`: Tout le domaine (ex: `site:docs.fastapi.com`)
- `"domain"`: Domaine racine (ex: `site:fastapi.com`)
- `"page"`: Page exacte

---

#### Pattern 3.4: Enrichissement LLM + Scoring
**Cas d'usage**: Améliorer la requête et scorer la pertinence des résultats

```json
POST /api/v1/search
{
  "query": "comment déployer une api",
  "llm_enrichment": true,
  "relevance_scoring": true,
  "language": "fr",
  "max_results": 10
}
```

**Comportement**:
- `llm_enrichment`: Enrichit la requête (synonymes, contexte)
  - "comment déployer une api" → "API deployment production Docker Kubernetes server hosting"
- `relevance_scoring`: Score chaque résultat (0-1) selon pertinence avec la requête originale

---

### 4️⃣ `/api/v1/research/quick` - Réponse Rapide Documentée

#### Pattern 4.1: Recherche Ouverte (par défaut)
**Cas d'usage**: Question simple, recherche web automatique

```json
POST /api/v1/research/quick
{
  "query": "Quelle est la capitale du Japon?",
  "max_sources": 5,
  "include_citations": true,
  "timeout": 60
}
```

**Comportement**:
- Recherche web via SearXNG
- Extrait contenu de 5 sources
- Génère réponse avec citations `[1][2]`

---

#### Pattern 4.2: Sources Prioritaires (`priority`)
**Cas d'usage**: Privilégier certaines sources mais accepter compléments web si insuffisant

```json
POST /api/v1/research/quick
{
  "query": "Statistiques démographiques France 2024",
  "sources": {
    "urls": [
      "https://www.insee.fr",
      "https://data.gouv.fr/datasets/population"
    ],
    "strategy": "priority"
  },
  "max_sources": 8,
  "timeout": 90
}
```

**Comportement**:
1. Essaie d'extraire les URLs fournies en premier
2. Si insuffisant (< 8 sources) → Recherche web complémentaire
3. Génère réponse en privilégiant les sources fournies

**Utilisation**:
- Privilégier documentation officielle tout en acceptant tutoriels communautaires
- Partir de sources connues et compléter si nécessaire

---

#### Pattern 4.3: Sources Exclusives (`exclusive`)
**Cas d'usage**: UNIQUEMENT les sources fournies, pas de recherche web

```json
POST /api/v1/research/quick
{
  "query": "Obligations de signalisation temporaire sur chantiers",
  "sources": {
    "urls": [
      "https://legifrance.gouv.fr/codes/id/LEGISCTA000006177118",
      "https://www.securite-routiere.gouv.fr/reglementation-liee-la-route/signalisation-routiere",
      "https://demarches-securite-routes.cerema.fr"
    ],
    "strategy": "exclusive"
  },
  "max_sources": 10,
  "timeout": 120
}
```

**Comportement**:
- ❌ **Pas de recherche web** (SearXNG désactivé)
- ✅ Uniquement extraction des URLs fournies
- Si échec extraction → Erreur "No results from provided URLs (exclusive strategy)"

**Utilisation**:
- Réponses 100% fiables (réglementation, sources officielles)
- Contexte légal/médical où sources non vérifiées sont inacceptables
- Audit de conformité

---

#### Pattern 4.4: Compléments Web (`complement`)
**Cas d'usage**: Recherche web normale + ajout d'URLs spécifiques

```json
POST /api/v1/research/quick
{
  "query": "Tutoriel avancé FastAPI webhooks",
  "sources": {
    "urls": [
      "https://fastapi.tiangolo.com/advanced/events",
      "https://fastapi.tiangolo.com/advanced/websockets"
    ],
    "strategy": "complement"
  },
  "max_sources": 10,
  "timeout": 90
}
```

**Comportement**:
1. Recherche web normale via SearXNG
2. Ajoute les URLs fournies aux résultats
3. Extrait tout et génère réponse complète

**Utilisation**:
- Ajouter documentation officielle aux résultats web
- Garantir présence de sources clés dans la réponse

---

### 5️⃣ `/api/v1/research/deep` - Rapport de Recherche Approfondi

> **Note**: Deep Research utilise l'Intelligent Orchestrator avec architecture 4-phase (Exploration → Planning → Construction → Cohérence). Voir `DEEP_RESEARCH_ARCHITECTURE.md` pour détails techniques.

#### Pattern 5.1: Découverte Totale (Audit Systémique)
**Cas d'usage**: Recherche exploratoire complète, aucune source imposée

```json
POST /api/v1/research/deep
{
  "topic": "Audit systémique de la réglementation de signalisation routière française",
  "objectives": [
    "Identifier les top 50 failles critiques",
    "Détecter les patterns récurrents (ex: terme 'exceptionnel' mal défini)",
    "Analyser les failles systémiques",
    "Proposer des recommandations de réforme"
  ],
  "sources": {
    "required": [],
    "suggested": [],
    "exclusions": [],
    "domains_whitelist": []
  },
  "context": {
    "language": "fr",
    "min_sources": 30,
    "include_reports": true,
    "include_academic": true,
    "include_data": true
  },
  "max_steps": 20,
  "timeout": 600
}
```

**Comportement**:
- Phase 1: Exploration via SearXNG (découverte automatique)
- Phase 2: Construction section par section avec recherches ciblées
- Phase 3: Cohérence globale et croisement inter-sections
- Phase 4: Rapport final avec bibliographie complète

**Résultat**: Rapport structuré avec 5-10 sections, 30+ sources, bibliographie, métadonnées d'exécution

**Utilisation**:
- Études de marché complètes
- Audits réglementaires
- États de l'art techniques
- Dossiers de recherche académiques

---

#### Pattern 5.2: Sources Officielles UNIQUEMENT (`required`)
**Cas d'usage**: Rapport basé exclusivement sur sources de confiance

```json
POST /api/v1/research/deep
{
  "topic": "Évolution du droit du travail français 2024",
  "objectives": [
    "Recenser les changements législatifs",
    "Analyser l'impact sur les entreprises",
    "Identifier les nouvelles obligations employeurs"
  ],
  "sources": {
    "required": [
      "https://legifrance.gouv.fr",
      "https://travail-emploi.gouv.fr",
      "https://www.vie-publique.fr"
    ],
    "suggested": [],
    "exclusions": [],
    "domains_whitelist": []
  },
  "context": {
    "language": "fr",
    "min_sources": 15
  },
  "max_steps": 15,
  "timeout": 600
}
```

**Comportement**:
- ⚠️ **BLOQUANT**: Si extraction échoue sur une URL `required` → Erreur fatale
- Recherches ciblées uniquement sur ces domaines
- Garantit fiabilité 100%

**Utilisation**:
- Documents légaux/réglementaires
- Rapports de conformité
- Audits officiels
- Contextes nécessitant sources vérifiées

---

#### Pattern 5.3: Recherche Guidée (`suggested`)
**Cas d'usage**: Partir de sources connues + exploration complémentaire

```json
POST /api/v1/research/deep
{
  "topic": "État de l'art IA générative - Modèles de langage 2024",
  "objectives": [
    "Recenser les modèles LLM récents",
    "Comparer performances et capacités",
    "Analyser les tendances et innovations"
  ],
  "sources": {
    "required": [],
    "suggested": [
      "https://arxiv.org",
      "https://huggingface.co/papers",
      "https://openai.com/research",
      "https://www.anthropic.com/research"
    ],
    "exclusions": ["blog.*", "medium.com"],
    "domains_whitelist": []
  },
  "context": {
    "language": "en",
    "min_sources": 25,
    "include_academic": true
  },
  "max_steps": 20,
  "timeout": 600
}
```

**Comportement**:
1. Commence par extraire les URLs `suggested` (prioritaires)
2. Si insuffisant (< 25 sources) → Découverte complémentaire via SearXNG
3. Filtre les domaines dans `exclusions`

**Utilisation**:
- Recherches académiques avec sources de référence
- Documentation technique avec sites connus
- Veille technologique guidée

---

#### Pattern 5.4: Whitelist Domaines
**Cas d'usage**: Limiter la recherche à des domaines de confiance spécifiques

```json
POST /api/v1/research/deep
{
  "topic": "Données publiques françaises disponibles - Inventaire complet",
  "objectives": [
    "Recenser toutes les APIs gouvernementales",
    "Lister les datasets ouverts",
    "Documenter les conditions d'accès"
  ],
  "sources": {
    "required": [],
    "suggested": [],
    "exclusions": [],
    "domains_whitelist": [
      "gouv.fr",
      "data.gouv.fr",
      "insee.fr",
      "legifrance.gouv.fr",
      "service-public.fr"
    ]
  },
  "context": {
    "language": "fr",
    "min_sources": 20,
    "include_data": true
  },
  "max_steps": 15,
  "timeout": 600
}
```

**Comportement**:
- Découverte via SearXNG avec: `site:gouv.fr OR site:data.gouv.fr OR site:insee.fr...`
- Filtre **TOUS** résultats hors whitelist
- Garantit sources gouvernementales/officielles uniquement

**Utilisation**:
- Veille réglementaire gouvernementale
- Inventaire de ressources officielles
- Recherches nécessitant sources publiques uniquement

---

#### Pattern 5.5: Blacklist Domaines (`exclusions`)
**Cas d'usage**: Éviter sources non fiables (forums, blogs, réseaux sociaux)

```json
POST /api/v1/research/deep
{
  "topic": "Études scientifiques - Efficacité vaccins COVID-19",
  "objectives": [
    "Recenser études peer-reviewed",
    "Analyser les résultats cliniques",
    "Synthétiser le consensus scientifique"
  ],
  "sources": {
    "required": [],
    "suggested": ["https://pubmed.ncbi.nlm.nih.gov", "https://www.thelancet.com"],
    "exclusions": [
      "forum.*",
      "blog.*",
      "facebook.com",
      "twitter.com",
      "*reddit.com",
      "quora.com",
      "medium.com"
    ],
    "domains_whitelist": []
  },
  "context": {
    "language": "en",
    "min_sources": 30,
    "include_academic": true
  },
  "max_steps": 20,
  "timeout": 600
}
```

**Comportement**:
- Découvre sources via SearXNG
- Filtre URLs matchant les patterns `exclusions` (wildcards `*` supportés)
- Privilégie sources académiques/scientifiques

**Utilisation**:
- Recherches scientifiques (éviter opinions)
- Analyses factuelles (éviter biais)
- Documentation technique (éviter blogs amateurs)

---

## 🔗 Patterns de Chaînage (2-3 endpoints)

> **Pour développeurs d'agents**: Ces patterns montrent comment orchestrer plusieurs endpoints pour créer des workflows complexes.

### Pattern A: Search → Extract → Vision
**Cas d'usage**: Trouver pages avec graphiques → Extraire images → Analyser données visuelles

```json
// === ÉTAPE 1: Rechercher pages avec graphiques ===
POST /api/v1/search
{
  "query": "statistiques démographiques France graphiques INSEE",
  "dorking": true,
  "categories": ["general", "images"],
  "max_results": 5
}

// Résultat:
// {
//   "results": [
//     {"url": "https://insee.fr/stats-2024", "title": "Statistiques 2024"},
//     {"url": "https://data.gouv.fr/demo-france", "title": "Démographie"}
//   ]
// }

// === ÉTAPE 2: Extraire contenu + images de chaque page ===
POST /api/v1/extract
{
  "url": "https://insee.fr/stats-2024",
  "options": {
    "extract_images": true,
    "extract_links": false
  }
}

// Résultat:
// {
//   "images": [
//     "https://insee.fr/graphs/population-2024.png",
//     "https://insee.fr/charts/age-pyramid.png"
//   ]
// }

// === ÉTAPE 3: Analyser chaque graphique ===
for each image_url in images:
  POST /api/v1/vision
  {
    "image_url": image_url,
    "prompt": "Extraire toutes les données numériques de ce graphique. Retourner en JSON avec format: {labels: [], values: []}",
    "system_prompt": "Tu es un expert en extraction de données visuelles. Retourne uniquement du JSON valide.",
    "temperature": 0.0,
    "max_tokens": 1000
  }

// Résultat final: Données structurées de tous les graphiques
```

**Gain**: Pipeline complet d'extraction de données visuelles sans intervention manuelle

---

### Pattern B: Search (dorking) → Research/Quick (exclusive)
**Cas d'usage**: Découvrir sources officielles → Répondre uniquement avec celles-ci

```json
// === ÉTAPE 1: Découvrir sources officielles ===
POST /api/v1/search
{
  "query": "réglementation signalisation routière obligations chantiers",
  "dorking": true,
  "language": "fr",
  "max_results": 10
}

// Le dorking active automatiquement: site:gouv.fr OR site:legifrance.gouv.fr

// Résultat:
// {
//   "results": [
//     {"url": "https://legifrance.gouv.fr/codes/id/LEGISCTA000006177118"},
//     {"url": "https://www.securite-routiere.gouv.fr/reglementation-liee-la-route"},
//     {"url": "https://demarches-securite-routes.cerema.fr"}
//   ]
// }

// === ÉTAPE 2: Réponse avec sources officielles UNIQUEMENT ===
POST /api/v1/research/quick
{
  "query": "Quelles sont les obligations légales de signalisation temporaire sur chantiers routiers?",
  "sources": {
    "urls": [
      "https://legifrance.gouv.fr/codes/id/LEGISCTA000006177118",
      "https://www.securite-routiere.gouv.fr/reglementation-liee-la-route",
      "https://demarches-securite-routes.cerema.fr"
    ],
    "strategy": "exclusive"
  },
  "max_sources": 10,
  "timeout": 120
}

// Résultat: Réponse 100% basée sur sources officielles
// Zéro risque d'informations non vérifiées
```

**Gain**: Workflow découverte + garantie de fiabilité

**Utilisation**:
- Chatbots légaux/réglementaires
- Assistants conformité
- Documentation officielle

---

### Pattern C: Extract (avec links) → Research/Quick (priority)
**Cas d'usage**: Partir d'une page index → Extraire tous les liens → Répondre en privilégiant la doc

```json
// === ÉTAPE 1: Extraire page index + tous les liens ===
POST /api/v1/extract
{
  "url": "https://docs.fastapi.com/tutorial",
  "options": {
    "extract_links": true,
    "extract_images": false
  }
}

// Résultat:
// {
//   "links": [
//     {"url": "https://docs.fastapi.com/advanced/security", "text": "Security"},
//     {"url": "https://docs.fastapi.com/deployment", "text": "Deployment"},
//     {"url": "https://docs.fastapi.com/tutorial/database", "text": "Database"}
//   ]
// }

// === ÉTAPE 2: Recherche en privilégiant ces pages ===
POST /api/v1/research/quick
{
  "query": "Comment sécuriser une API FastAPI avec OAuth2 et JWT?",
  "sources": {
    "urls": [
      "https://docs.fastapi.com/advanced/security",
      "https://docs.fastapi.com/tutorial/security",
      "https://docs.fastapi.com/deployment"
    ],
    "strategy": "priority"
  },
  "max_sources": 8,
  "timeout": 90
}

// Comportement:
// 1. Extrait d'abord la doc officielle (priority)
// 2. Si insuffisant, complète avec recherche web (tutoriels, exemples)
// 3. Génère réponse qui privilégie doc officielle
```

**Gain**: Combine autorité de la doc officielle + richesse des exemples communautaires

---

### Pattern D: Search (site targeting) → Extract → Research/Quick (complement)
**Cas d'usage**: Cibler documentation d'un site → Extraire → Compléter avec web

```json
// === ÉTAPE 1: Rechercher sur site spécifique ===
POST /api/v1/search
{
  "query": "API communes départements endpoints documentation",
  "target_url": "https://geo.api.gouv.fr",
  "scope": "site",
  "max_results": 5
}

// Résultat:
// {
//   "results": [
//     {"url": "https://geo.api.gouv.fr/decoupage-administratif/communes"},
//     {"url": "https://geo.api.gouv.fr/decoupage-administratif/departements"}
//   ]
// }

// === ÉTAPE 2: Extraire contenu de chaque page doc ===
for each url in results:
  POST /api/v1/extract
  {
    "url": url,
    "options": {"clean_html": true}
  }

// === ÉTAPE 3: Réponse avec doc site + compléments web ===
POST /api/v1/research/quick
{
  "query": "Comment utiliser l'API Geo pour récupérer les communes d'un département?",
  "sources": {
    "urls": [
      "https://geo.api.gouv.fr/decoupage-administratif/communes",
      "https://geo.api.gouv.fr/decoupage-administratif/departements"
    ],
    "strategy": "complement"
  },
  "max_sources": 10,
  "timeout": 90
}

// Comportement:
// 1. Recherche web normale (exemples, tutoriels)
// 2. Ajoute les pages de doc officielle
// 3. Génère réponse complète (doc + exemples pratiques)
```

**Gain**: Documentation officielle + exemples de code réels

---

### Pattern E: Vision (OCR) → Extract → Research/Quick
**Cas d'usage**: Screenshot d'une page → OCR URL → Extraire → Répondre

```json
// === ÉTAPE 1: OCR sur screenshot pour récupérer URLs ===
POST /api/v1/vision
{
  "image_url": "https://example.com/screenshot-doc.png",
  "prompt": "Extraire TOUTES les URLs visibles dans cette image. Retourner une liste JSON: [\"url1\", \"url2\", ...]",
  "temperature": 0.0,
  "max_tokens": 500
}

// Résultat:
// {
//   "analysis": "[\"https://docs.example.com/api\", \"https://github.com/example/repo\"]"
// }

// === ÉTAPE 2: Extraire contenu des URLs ===
for each url in urls:
  POST /api/v1/extract
  {
    "url": url,
    "options": {"clean_html": true}
  }

// === ÉTAPE 3: Répondre avec contenu extrait ===
POST /api/v1/research/quick
{
  "query": "Comment cette API fonctionne-t-elle? Quels sont les endpoints principaux?",
  "sources": {
    "urls": ["https://docs.example.com/api", "https://github.com/example/repo"],
    "strategy": "exclusive"
  }
}
```

**Gain**: Workflow complet depuis capture d'écran (zéro copier-coller manuel)

**Utilisation**:
- Agents desktop qui capturent l'écran
- Bots Slack/Discord avec screenshots
- Outils de documentation automatique

---

### Pattern F: Search → API Navigator → Research/Quick
**Cas d'usage**: Découvrir API → Explorer endpoints → Répondre avec données

```json
// === ÉTAPE 1: Découvrir APIs gouvernementales ===
POST /api/v1/search
{
  "query": "API gouvernementale française communes population",
  "dorking": true,
  "max_results": 3
}

// Résultat: https://geo.api.gouv.fr

// === ÉTAPE 2: Explorer l'API découverte ===
POST /api/v1/api-navigator
{
  "api_base_url": "https://geo.api.gouv.fr",
  "api_doc_url": "https://geo.api.gouv.fr/decoupage-administratif",
  "user_query": "10 communes françaises les plus peuplées"
}

// Résultat:
// {
//   "results": [
//     {"nom": "Paris", "population": 2113705},
//     {"nom": "Marseille", "population": 877215},
//     ...
//   ]
// }

// === ÉTAPE 3: Répondre avec contexte enrichi ===
POST /api/v1/research/quick
{
  "query": "Quelle est la commune la plus peuplée de France et de combien dépasse-t-elle Marseille?",
  "sources": {
    "urls": ["https://geo.api.gouv.fr/communes"],
    "strategy": "priority"
  }
}
```

**Gain**: Découverte automatique d'APIs + exploitation des données

**Utilisation**:
- Agents de data analysis
- Chatbots avec accès APIs publiques
- Outils d'exploration de données gouvernementales

---

### Pattern G: Extract (liens + images) → Vision (multiple) → Research/Quick
**Cas d'usage**: Page complexe → Extraire tout → Analyser graphiques → Synthèse globale

```json
// === ÉTAPE 1: Extraire page complète ===
POST /api/v1/extract
{
  "url": "https://insee.fr/rapport-economique-2024",
  "options": {
    "extract_images": true,
    "extract_links": true
  }
}

// Résultat:
// {
//   "content": "Rapport économique 2024...",
//   "images": ["chart1.png", "graph2.png", "table3.png"],
//   "links": ["annexe-1", "annexe-2"]
// }

// === ÉTAPE 2: Analyser chaque graphique ===
graph_data = []
for each image in images:
  POST /api/v1/vision
  {
    "image_url": image,
    "prompt": "Extraire toutes les données numériques. Format JSON."
  }
  graph_data.append(response)

// === ÉTAPE 3: Synthèse complète ===
POST /api/v1/research/quick
{
  "query": "Quelles sont les principales tendances économiques 2024 selon ce rapport? Synthétiser avec chiffres clés.",
  "sources": {
    "urls": ["https://insee.fr/rapport-economique-2024"],
    "strategy": "exclusive"
  }
}
```

**Gain**: Extraction totale contenu texte + données visuelles

---

## 🎯 Cas d'Usage Métier

### 1. Veille Réglementaire Automatisée

**Objectif**: Monitorer changements législatifs dans un domaine

**Workflow**:
```json
// Quotidien: Découverte de nouveaux textes
POST /api/v1/search
{
  "query": "droit du travail france",
  "dorking": true,
  "time_range": "day",
  "max_results": 20
}

// Hebdomadaire: Analyse approfondie des changements
POST /api/v1/research/deep
{
  "topic": "Évolutions réglementaires droit du travail - Semaine du [date]",
  "sources": {
    "domains_whitelist": ["legifrance.gouv.fr", "travail-emploi.gouv.fr"]
  },
  "context": {
    "date_range": "week"
  }
}
```

**Agent recommandé**: Scheduled job (cron) qui compare rapports semaine N vs N-1

---

### 2. Extraction de Données Publiques

**Objectif**: Récupérer datasets gouvernementaux pour analyse

**Workflow**:
```json
// Découvrir APIs de données
POST /api/v1/search
{
  "query": "API données publiques INSEE population",
  "target_url": "https://api.gouv.fr",
  "scope": "site"
}

// Explorer et récupérer données
POST /api/v1/api-navigator
{
  "api_base_url": "https://api.insee.fr",
  "user_query": "Télécharger données population par département"
}
```

**Agent recommandé**: Pipeline ETL automatisé

---

### 3. Audit de Conformité Documentaire

**Objectif**: Vérifier qu'un site respecte les réglementations

**Workflow**:
```json
// Extraire pages du site à auditer
POST /api/v1/extract
{
  "url": "https://example.com/mentions-legales",
  "options": {"extract_links": true}
}

// Vérifier conformité avec textes officiels
POST /api/v1/research/quick
{
  "query": "Ce site respecte-t-il les obligations RGPD? Lister les manquements.",
  "sources": {
    "urls": ["https://example.com/mentions-legales", "https://cnil.fr/rgpd"],
    "strategy": "complement"
  }
}
```

---

### 4. Génération de Documentation Technique

**Objectif**: Créer doc à partir de code source + APIs

**Workflow**:
```json
// Analyser structure du projet
POST /api/v1/search
{
  "query": "API endpoints",
  "target_url": "https://github.com/user/project",
  "scope": "site"
}

// Générer rapport complet
POST /api/v1/research/deep
{
  "topic": "Documentation complète API [project]",
  "sources": {
    "suggested": ["https://github.com/user/project"]
  },
  "output_format": {
    "structure": "report",
    "sections": ["Architecture", "Endpoints", "Authentication", "Examples"]
  }
}
```

---

### 5. Analyse Concurrentielle

**Objectif**: Comparer produits/services concurrents

**Workflow**:
```json
POST /api/v1/research/deep
{
  "topic": "Analyse concurrentielle - Solutions de [domaine]",
  "objectives": [
    "Identifier les acteurs principaux",
    "Comparer fonctionnalités et prix",
    "Analyser forces et faiblesses",
    "Positionner notre offre"
  ],
  "sources": {
    "exclusions": ["forum.*", "blog.*"]  // Éviter opinions
  },
  "context": {
    "min_sources": 30,
    "include_data": true
  }
}
```

---

## 🤖 Guide pour Développeurs d'Agents

### Principes de Design

#### 1. Orchestration Progressive
**Ne pas utiliser deep research si quick suffit**

```python
# ❌ Mauvais: Toujours utiliser deep research
response = await client.post("/api/v1/research/deep", json={"topic": query})

# ✅ Bon: Escalade progressive
# 1. Essayer quick d'abord
response = await client.post("/api/v1/research/quick", json={"query": query})

# 2. Si confiance faible ou sources insuffisantes → deep
if response["confidence"] == "low" or len(response["sources"]) < 5:
    response = await client.post("/api/v1/research/deep", json={"topic": query})
```

**Gain**: Temps de réponse optimisé (15s vs 300s)

---

#### 2. Gestion des Timeouts

```python
# Configuration par complexité de tâche
TIMEOUTS = {
    "extract": 30,
    "vision": 15,
    "search": 30,
    "research_quick": 90,
    "research_deep": 600
}

# Retry avec timeout augmenté si échec
async def resilient_request(endpoint, data, max_retries=2):
    timeout = TIMEOUTS[endpoint]
    for attempt in range(max_retries):
        try:
            return await client.post(f"/api/v1/{endpoint}",
                                    json=data,
                                    timeout=timeout)
        except TimeoutError:
            timeout *= 1.5  # Augmenter timeout
            if attempt == max_retries - 1:
                raise
```

---

#### 3. Parallélisation des Extractions

```python
# ❌ Mauvais: Séquentiel
for url in urls:
    result = await extract(url)
    results.append(result)

# ✅ Bon: Parallèle (max 5 concurrent)
import asyncio
from itertools import islice

async def extract_batch(urls, batch_size=5):
    results = []
    for batch in batched(urls, batch_size):
        batch_results = await asyncio.gather(
            *[extract(url) for url in batch],
            return_exceptions=True
        )
        results.extend(batch_results)
    return results
```

---

#### 4. Stratégie de Sources Dynamique

```python
def determine_source_strategy(query: str, context: dict) -> dict:
    """
    Détermine automatiquement la stratégie de sources appropriée
    """
    # Détection de mots-clés légaux/réglementaires
    legal_keywords = ["loi", "article", "code", "réglementation", "obligation"]
    if any(kw in query.lower() for kw in legal_keywords):
        return {
            "sources": {
                "strategy": "exclusive",
                "urls": discover_official_sources(query)  # Découverte préalable
            }
        }

    # Détection de documentation technique
    if "documentation" in query.lower() or "doc" in context.get("type", ""):
        return {
            "sources": {
                "strategy": "priority",
                "urls": context.get("doc_urls", [])
            }
        }

    # Par défaut: recherche ouverte
    return {"sources": None}

# Usage
config = determine_source_strategy(user_query, context)
response = await client.post("/api/v1/research/quick", json={
    "query": user_query,
    **config
})
```

---

#### 5. Cache Intelligent

```python
from functools import lru_cache
import hashlib

@lru_cache(maxsize=100)
async def cached_extract(url: str):
    """Cache les extractions par URL"""
    return await extract(url)

def cache_key(endpoint: str, params: dict) -> str:
    """Génère clé de cache"""
    return hashlib.md5(
        f"{endpoint}:{json.dumps(params, sort_keys=True)}".encode()
    ).hexdigest()

# Cache Redis pour agents distribués
import aioredis

redis = await aioredis.create_redis_pool('redis://localhost')

async def cached_request(endpoint: str, data: dict, ttl: int = 3600):
    key = cache_key(endpoint, data)

    # Vérifier cache
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)

    # Requête + mise en cache
    result = await client.post(f"/api/v1/{endpoint}", json=data)
    await redis.setex(key, ttl, json.dumps(result))
    return result
```

---

### Patterns d'Agents Recommandés

#### Agent 1: Assistant Réglementaire
```python
class RegulatoryAssistant:
    """
    Agent spécialisé dans les questions légales/réglementaires
    """

    async def answer(self, query: str) -> dict:
        # 1. Découvrir sources officielles
        official_sources = await self.discover_official_sources(query)

        # 2. Réponse exclusive sur sources officielles
        response = await client.post("/api/v1/research/quick", json={
            "query": query,
            "sources": {
                "urls": official_sources,
                "strategy": "exclusive"
            },
            "timeout": 120
        })

        # 3. Si échec, fallback sur deep research
        if not response["success"]:
            response = await client.post("/api/v1/research/deep", json={
                "topic": query,
                "sources": {
                    "domains_whitelist": ["gouv.fr", "legifrance.gouv.fr"]
                }
            })

        return response

    async def discover_official_sources(self, query: str) -> list:
        """Découvre sources officielles via dorking"""
        search_result = await client.post("/api/v1/search", json={
            "query": query,
            "dorking": True,
            "max_results": 10
        })

        # Filtrer uniquement .gouv.fr
        return [
            r["url"] for r in search_result["results"]
            if "gouv.fr" in r["url"]
        ]
```

---

#### Agent 2: Extracteur de Données Visuelles
```python
class VisualDataExtractor:
    """
    Agent pour extraire données de graphiques/tableaux
    """

    async def extract_from_url(self, url: str) -> dict:
        # 1. Extraire page + images
        page = await client.post("/api/v1/extract", json={
            "url": url,
            "options": {"extract_images": True}
        })

        # 2. Analyser chaque image
        extracted_data = []
        for img_url in page["images"]:
            vision_result = await client.post("/api/v1/vision", json={
                "image_url": img_url,
                "prompt": "Extraire données en JSON: {labels: [], values: []}",
                "temperature": 0.0
            })

            try:
                data = json.loads(vision_result["analysis"])
                extracted_data.append({
                    "image": img_url,
                    "data": data
                })
            except json.JSONDecodeError:
                continue

        return {
            "url": url,
            "text": page["content"],
            "visual_data": extracted_data
        }
```

---

#### Agent 3: Veilleur Automatisé
```python
class AutomatedMonitor:
    """
    Agent de veille qui compare états avant/après
    """

    def __init__(self, topics: list, check_interval: int = 86400):
        self.topics = topics
        self.check_interval = check_interval  # 24h par défaut
        self.previous_state = {}

    async def run(self):
        while True:
            for topic in self.topics:
                # Recherche actuelle
                current = await self.research(topic)

                # Comparaison avec état précédent
                if topic in self.previous_state:
                    changes = self.detect_changes(
                        self.previous_state[topic],
                        current
                    )

                    if changes:
                        await self.notify(topic, changes)

                # MAJ état
                self.previous_state[topic] = current

            await asyncio.sleep(self.check_interval)

    async def research(self, topic: str) -> dict:
        return await client.post("/api/v1/research/deep", json={
            "topic": topic,
            "context": {"date_range": "day"},
            "max_steps": 10,
            "timeout": 300
        })

    def detect_changes(self, old: dict, new: dict) -> list:
        """Détecte changements significatifs"""
        changes = []

        # Nouvelles sources
        old_urls = {s["url"] for s in old.get("sources", {}).get("websites", [])}
        new_urls = {s["url"] for s in new.get("sources", {}).get("websites", [])}

        added = new_urls - old_urls
        if added:
            changes.append({"type": "new_sources", "urls": list(added)})

        return changes
```

---

### Best Practices pour Agents

1. **Toujours gérer les erreurs**
   - Timeouts
   - Sources indisponibles
   - Formats de réponse inattendus

2. **Implémenter fallbacks**
   - Quick → Deep si confiance faible
   - Extract → Search si URL inaccessible
   - Priority → Exclusive si sources critique manquantes

3. **Logger les décisions**
   ```python
   logger.info(f"Strategy: exclusive (detected legal keywords)")
   logger.info(f"Sources discovered: {len(sources)}")
   logger.info(f"Confidence: {response['confidence']}")
   ```

4. **Monitorer les coûts**
   - Tracker temps de réponse par endpoint
   - Compter tokens LLM utilisés
   - Mesurer cache hit ratio

5. **Tester avec données réelles**
   - Ne pas supposer format de réponse
   - Tester avec timeouts courts
   - Valider avec sources indisponibles

---

## 📊 Tableau Récapitulatif

| Pattern | Endpoints | Paramètres Clés | Temps | Utilisation |
|---------|-----------|-----------------|-------|-------------|
| **Extraction simple** | 1 | `extract` | 2-30s | Contenu unique |
| **Vision OCR** | 1 | `vision` + `temperature=0` | 1-5s | Documents scannés |
| **Search dorking** | 1 | `search` + `dorking=true` | 5-15s | Découverte ciblée |
| **Quick exclusive** | 1 | `quick` + `strategy=exclusive` | 30-90s | Fiabilité 100% |
| **Deep discovery** | 1 | `deep` + `required=[]` | 60-600s | Audit systémique |
| **Deep whitelist** | 1 | `deep` + `domains_whitelist` | 60-600s | Sources officielles |
| **Search→Extract→Vision** | 3 | Chaînage | 30-120s | Données visuelles |
| **Search→Quick** | 2 | Découverte + exclusive | 45-120s | Workflow fiable |
| **Extract→Quick** | 2 | Links + priority | 30-90s | Doc officielle + web |

---

## 🚀 Pour Aller Plus Loin

- **Documentation API complète**: `/docs` (Swagger UI)
- **Architecture Deep Research**: `DEEP_RESEARCH_ARCHITECTURE.md`
- **Guide des Endpoints**: `ENDPOINTS_GUIDE.md`
- **Exemples Postman**: `examples/webtools-collection.json`

---

**Version**: 1.0.0
**Dernière mise à jour**: 2025-11-19
**Maintenu par**: [@nic01asFr](https://github.com/nic01asFr)
