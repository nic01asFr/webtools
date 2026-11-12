# Enregistrement et rejeu de la collecte

Module : `app/research/replay.py`. Objectif : mesurer un changement de
synthese ou de citations a collecte figee. Le web cesse d'etre une source de
bruit, les moteurs fragiles ne sont plus sollicites, un run ne coute que les
appels LLM.

## Utilisation

Variables d'environnement (lues par `read_replay_env()`) :

- `WEBTOOLS_REPLAY_MODE` : `record` ou `replay` (absent : desactive).
- `WEBTOOLS_REPLAY_DIR` : repertoire de la tache (un repertoire par tache).

1. Enregistrer : `record`, run normal sur le web reel. Chaque recherche est
   ecrite dans `<dir>/search/<empreinte>.json`, chaque page dans
   `<dir>/pages/<empreinte>.json` (JSON lisible).
2. Rejouer : `replay`, autant de runs que voulu ; seuls les appels LLM sont
   payes.

Le rejeu echoue explicitement (`ReplayMissError`, qui nomme la requete et le
repertoire) sur une requete inconnue. Le pipeline avale souvent les
exceptions de collecte : chaque enveloppe expose donc le compteur `missed`,
a verifier egal a zero apres un run (`served` et `recorded` sont aussi
exposes).

## Cles d'enregistrement

- Recherche : empreinte des parametres normalises (requete aux espaces
  reduits, langue, moteurs, categories, plage temporelle, nombre de
  resultats, pages).
- Extraction : URL normalisee seule (schema et hote en minuscules, sans
  fragment ni barre finale). Les options d'extraction varient d'un appel a
  l'autre sans changer la page.

## Limite

Un changement de collecte (requetes generees, nombre de resultats, choix des
URLs : lot V3-4) invalide l'enregistrement : les requetes ne correspondent
plus et le rejeu echoue. Ce type de changement se mesure sur le web reel,
avec un nouvel enregistrement.

## Branchement (hors de ce lot)

A l'endroit ou l'orchestrateur est construit (endpoint ou harnais) :

    from app.research.replay import build_replay_clients, read_replay_env
    mode, directory = read_replay_env()
    if mode:
        search_client, extractor_manager = build_replay_clients(
            mode, directory, search_client, extractor_manager)
