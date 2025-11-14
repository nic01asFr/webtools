# Deep Research Architecture - Documentation Complète

## 🎯 Vue d'ensemble

Le système `/api/v1/research/deep` a été entièrement refondé pour implémenter une **architecture itérative section par section** qui construit des rapports de recherche approfondis de manière progressive et traçable.

### Principes Fondamentaux

1. **Exploration avant planification** : Le système évalue d'abord le champ disponible avant de créer un plan détaillé
2. **Construction itérative** : Chaque section est construite indépendamment avec son propre cycle de recherche
3. **Croisement de données** : Les sections partagent et référencent les données communes
4. **Cohérence globale** : Revue systématique des liens entre sections
5. **Traces complètes** : Chaque phase est documentée et traçable

---

## 🔄 Architecture en 4 Phases

### Phase 1 : EXPLORATION + PLAN DÉTAILLÉ

#### 1.1 Exploration Initiale (`_exploratory_phase`)
**Objectif** : Évaluer rapidement le champ disponible avant planification

**Processus** :
- Recherche restreinte (8 sources max)
- Extraction sous-thèmes des snippets
- Évaluation richesse du champ (rich/moderate/limited)
- Génération aperçu du contenu disponible

**Output** :
```json
{
  "sources": [{"url": "...", "title": "...", "snippet": "..."}],
  "field_assessment": "rich|moderate|limited",
  "topics_found": ["sous-thème1", "sous-thème2"],
  "data_preview": {"snippets": [...]}
}
```

#### 1.2 Planification Détaillée (`_create_detailed_plan`)
**Objectif** : Créer un canvas complet avec structure et enchaînements

**Analyse Multi-Critères** (5 dimensions, échelle 1-5) :
1. **Complexité du sujet** : Simple → Multidimensionnel
2. **Spécificité demandée** : Large → Très précis
3. **Format** : Résumé → Étude approfondie
4. **Profondeur temporelle** : Point dans le temps → Évolution historique
5. **Interconnexions** : Sujet isolé → Analyse systémique

**Score global** → Détermine ampleur :
- 1.0-2.0 : Rapport CONCIS (1-2 sections, 500-1000 mots)
- 2.1-3.0 : Rapport STANDARD (2-3 sections, 1000-1500 mots)
- 3.1-4.0 : Rapport DÉTAILLÉ (3-5 sections, 1500-2500 mots)
- 4.1-5.0 : Étude APPROFONDIE (4-7 sections, 2500-4000 mots)

**Output** :
```json
{
  "complexity_analysis": {
    "overall_score": 3.8,
    "target_length": "détaillé",
    "estimated_words": 2200
  },
  "sections": ["Introduction", "Analyse", "Conclusion"],
  "section_targets": {
    "Introduction": {
      "words_target": 400,
      "depth": "moderate",
      "objectives": ["Présenter le contexte"],
      "key_questions": ["Qu'est-ce que X?"]
    }
  },
  "narrative_flow": [
    {
      "from_section": "Introduction",
      "to_section": "Analyse",
      "transition_type": "zoom-in",
      "rationale": "Après contexte, détailler aspects"
    }
  ],
  "search_strategy": {
    "total_sources_needed": 12,
    "sources_per_section": 4,
    "search_depth": "standard"
  }
}
```

---

### Phase 2 : CONSTRUCTION ITÉRATIVE SECTION PAR SECTION

**Boucle pour chaque section** avec 4 étapes :

#### 2.1 Recherches Ciblées (`_section_research_phase`)
**Objectif** : Trouver sources spécifiques pour cette section

**Processus** :
- Requête enrichie : `{query} + {section_name} + {key_questions}`
- Nombre de sources adapté à la profondeur :
  - light: 3 sources
  - moderate: 5 sources
  - deep: 8 sources

**Output** :
```json
{
  "sources": [{"url": "...", "title": "...", "snippet": "..."}],
  "query_used": "Rust langage Caractéristiques Quelles fonctionnalités?"
}
```

#### 2.2 Extraction Sources (`_section_extraction_phase`)
**Objectif** : Extraire et nettoyer le contenu des URLs

**Processus** :
- Utilise `ExtractorManager` avec `AdvancedContentCleaner`
- Nettoyage HTML (suppression nav, footer, ads)
- Préservation structure (headings, listes, tables)
- Extraction métadonnées (auteur, date, description)

**Output** :
```json
[
  {
    "source": "https://...",
    "title": "...",
    "content": "Contenu nettoyé et structuré",
    "metadata": {"author": "...", "date": "..."}
  }
]
```

#### 2.3 Croisement Données (`_cross_reference_data`)
**Objectif** : Enrichir avec connexions vers autres sections

**Processus** :
- Pour chaque donnée, chercher dans sections existantes
- Identifier sources partagées
- Créer références croisées

**Output** :
```json
[
  {
    "source": "https://...",
    "content": "...",
    "cross_references": [
      {"section": "Introduction", "note": "Même source utilisée"}
    ]
  }
]
```

#### 2.4 Synthèse Section (`_synthesize_single_section`)
**Objectif** : Générer contenu rédigé de la section

**Processus** :
- Sélection sémantique des chunks pertinents
- Prompt adapté à la profondeur (light/moderate/deep)
- Génération avec citations `[SOURCE:url]`

**Instructions qualitatives** (pas quantitatives) :
- **light** : "2-3 paragraphes concis allant à l'essentiel"
- **moderate** : "4-6 paragraphes équilibrés et informatifs"
- **deep** : "6-10 paragraphes détaillés explorant tous les aspects"

**Output** : Contenu texte avec citations

---

### Phase 3 : COHÉRENCE GLOBALE PARTIE PAR PARTIE

#### 3.1 Analyse Inter-Sections (`_analyze_intersections`)
**Objectif** : Identifier améliorations de cohérence

**Stratégie adaptative** :
- **≤ 10 sections** : Analyse globale directe
- **> 10 sections** : Analyse distribuée par groupes de 8

**Processus (distribuée)** :
1. Diviser sections en groupes de 8
2. Analyser cohérence intra-groupe
3. Analyser transitions inter-groupes
4. Agréger résultats

**Output** :
```json
{
  "improvements": [
    {
      "type": "transition",
      "between_sections": ["Section A", "Section B"],
      "issue": "Passage abrupt",
      "suggestion": "Ajouter phrase de transition",
      "priority": "medium"
    }
  ],
  "redundancies": [],
  "coherence_score": 82
}
```

#### 3.2 Application Améliorations (`_apply_coherence_improvements`)
**Objectif** : Appliquer les améliorations identifiées

**Processus** :
- Traiter améliorations prioritaires (high)
- Limiter à 3 max pour éviter sur-modification
- Ajouter transitions en fin de section

---

### Phase 4 : FINALISATION

#### 4.1 Assemblage Final (`_final_assembly`)
**Objectif** : Combiner toutes les sections en rapport structuré

**Processus** :
- Collecte sections dans l'ordre du plan
- Génération titre et summary
- Calcul métadonnées (word_count, sources_count)
- Conversion citations → bibliographie numérotée

**Output** :
```json
{
  "type": "report",
  "title": "Analyse: Rust Langage",
  "summary": "Rust est un langage de programmation...",
  "sections": [
    {
      "title": "Introduction",
      "content": "...",
      "data": [...],
      "metadata": {"word_count": 480, "sources_count": 5}
    }
  ],
  "bibliography": [
    {"id": 1, "url": "...", "title": "Source 1 - rust-lang.org"}
  ],
  "metadata": {
    "total_word_count": 2765,
    "sections_count": 5,
    "complexity_score": 3.8
  }
}
```

#### 4.2 Traces Complètes (`_add_complete_traces`)
**Objectif** : Documenter tout le processus de recherche

**Output** :
```json
{
  "research_traces": {
    "exploration_phase": {
      "sources_discovered": 8,
      "field_assessment": "rich",
      "topics_identified": ["ownership", "concurrency"]
    },
    "planning_phase": {
      "complexity_analysis": {...},
      "sections_planned": [...],
      "narrative_flow": [...]
    },
    "construction_phase": {
      "sections_built": [...],
      "total_sources_collected": 25
    },
    "coherence_phase": {
      "improvements_applied": "..."
    }
  }
}
```

---

## 🧠 Gestion de la Mémoire et du Contexte

### Architecture Distribuée

**Scalabilité** :
- ✅ **Phase 2** : Chaque section = 1 appel LLM indépendant → **ILLIMITÉ**
- ✅ **Phase 3** : Groupes de 8 sections → Supporte **1000+ sections**
- ✅ **Phase 4** : Assemblage mécanique (pas d'appel LLM) → **ILLIMITÉ**

### ExecutionContext (Mémoire Partagée)

```python
class ExecutionContext:
    def __init__(self, query: str):
        self.query = query
        self.steps: List[Dict] = []
        self.datasets: Dict[str, List[Dict]] = {}
        self.discovered_sources: List[str] = []

        self.final_content: Dict[str, Any] = {
            "sections": {},  # Contenu de chaque section
            "global_metadata": {
                "sources_used": [],
                "extraction_timestamps": [],
                "all_structured_data": []
            }
        }
```

**Avantages** :
1. Accumulation progressive section par section
2. Accès partagé aux données entre sections
3. Pas de limite de fenêtre de contexte LLM
4. Traçabilité complète

---

## 📊 Résultats Validés

### Test Vue.js 3 (2min31)
- ✅ 7 sections, 2960 mots
- ✅ 22 sources bibliographiques
- ✅ Citations numérotées [1], [2]
- ✅ Traces complètes

### Test Rust (2min41)
- ✅ 5 sections, 2765 mots
- ✅ 22 sources bibliographiques
- ✅ Contenu technique détaillé
- ✅ Cohérence narrative

### Capacité Théorique
- **Sections max** : 1000+ (avec approche distribuée)
- **Mots max** : 500,000+ (1500 pages)
- **Durée** : ~30s par section (scalable linéairement)

---

## 🛠️ Fichiers Clés

### Core Logic
- **`app/agents/intelligent_orchestrator.py`** (2838 lignes)
  - Classes : `IntelligentOrchestrator`, `ExecutionContext`, `Tool`
  - Méthodes principales : 10 nouvelles méthodes (lignes 2145-2838)

### Content Processing
- **`app/core/content_cleaner.py`**
  - Classe : `AdvancedContentCleaner`
  - Nettoyage HTML avancé avec préservation structure

### API Endpoint
- **`app/api/v1/endpoints/research_deep.py`**
  - Endpoint : `POST /api/v1/research/deep`
  - Intégration : `IntelligentOrchestrator`, `OutputFormatter`

---

## 🎯 Différences Clés vs. Ancien Système

| Aspect | Ancien | Nouveau |
|--------|--------|---------|
| **Planification** | Directe sans contexte | Exploration préalable + plan détaillé |
| **Construction** | Collecte globale → synthèse | Itérative section par section |
| **Cohérence** | Aucune revue | Analyse partie par partie |
| **Scalabilité** | Limité à ~10 sections | 1000+ sections (distribuée) |
| **Traces** | Basiques | Complètes (4 phases) |
| **Qualité** | Longueur arbitraire | Conséquence naturelle de l'analyse |

---

## 📝 Usage

### Requête Simple
```bash
curl -X POST "https://webtools.colaig.fr/api/v1/research/deep" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Vue.js 3",
    "max_steps": 3,
    "timeout": 180
  }'
```

### Requête Avancée
```bash
curl -X POST "https://webtools.colaig.fr/api/v1/research/deep" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Intelligence artificielle générative",
    "objectives": ["Expliquer les concepts", "Analyser les usages"],
    "output_format": {
      "structure": "report",
      "sections": ["Introduction", "Technologies", "Applications", "Défis"]
    },
    "max_steps": 10,
    "timeout": 300
  }'
```

---

## 🔮 Évolutions Futures Possibles

1. **Assemblage hiérarchique** : Pour rapports 50+ sections avec chapitres
2. **Cache sémantique** : Réutiliser recherches similaires
3. **Export formats** : PDF, DOCX, Markdown
4. **Visualisations** : Graphiques, diagrammes depuis données
5. **Collaboration** : Annotations, révisions, suggestions

---

**Version** : 2.0
**Date** : 2025-11-14
**Projet** : Webtools Deep Research System
