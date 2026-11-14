# Enregistrement et rejeu de la collecte

Module : `app/research/replay.py`. Objectif : mesurer un changement de
synthese ou de citations a collecte figee. Le web cesse d'etre une source de
bruit, les moteurs fragiles ne sont plus sollicites, un run ne coute que les
appels LLM.

## Utilisation

Variables d'environnement (lues par `read_replay_env()`) :

- `WEBTOOLS_REPLAY_MODE` : `record`, `replay` ou `fill` (absent : desactive).
- `WEBTOOLS_REPLAY_DIR` : repertoire d'enregistrement. Il est lu par le service,
  donc commun a toutes les taches d'un run : les fichiers sont nommes par empreinte
  de la requete ou de l'URL, plusieurs taches peuvent y coexister.

1. Enregistrer : `record`, run normal sur le web reel. Chaque recherche est
   ecrite dans `<dir>/search/<empreinte>.json`, chaque page dans
   `<dir>/pages/<empreinte>.json` (JSON lisible).
2. Rejouer : `replay`, autant de runs que voulu ; seuls les appels LLM sont
   payes.
3. Completer : `fill`, sert ce qui est enregistre et delegue au web reel,
   puis ecrit, ce qui manque (`recorded` compte ces complements). A lancer
   sur une copie de l'enregistrement, qu'il modifie.

Le rejeu echoue explicitement (`ReplayMissError`, qui nomme la requete et le
repertoire) sur une requete inconnue. Le pipeline avale souvent les
exceptions de collecte : chaque enveloppe expose donc le compteur `missed`,
a verifier egal a zero apres un run (`served` et `recorded` sont aussi
exposes).

## Cles d'enregistrement

- Recherche : empreinte des parametres normalises (requete aux espaces
  reduits, langue, categories, plage temporelle, nombre de resultats,
  pages). Les moteurs ne font PAS partie de la cle : la cascade les choisit
  selon leur disponibilite, qui change d'un run a l'autre.
- Extraction : URL normalisee seule (schema et hote en minuscules, sans
  fragment ni barre finale). Les options d'extraction varient d'un appel a
  l'autre sans changer la page.

## Le plan est rejoue

Les requetes de recherche viennent du plan redige par le LLM. Depuis le
13/11/2026, le plan est enregistre et rejoue comme la collecte
(`ReplayPlanLLM`, `<dir>/llm/<empreinte>.json`, cle : messages exacts du
plan) : deux rejeux partent du meme plan, donc des memes requetes. Les
autres appels LLM (redaction, verification, enrichissement) ne sont jamais
rejoues : ce sont eux qu'une comparaison sur rejeu mesure.

## Comparer deux configurations

1. Un run en `record` (configuration A ou B, peu importe).
2. N rejeux par configuration, en `replay`, chacun dans son repertoire de
   sortie citecheck.
3. Evaluer chaque rejeu (`citations.py evaluer`), puis
   `citations.py ecart --a <evaluations A> --b <evaluations B>` : ecart
   apparie par tache, IC95 par bootstrap sur les taches, et bruit entre
   repetitions d'une meme configuration. Un ecart du meme ordre que le bruit
   ne prouve rien.

## Diagnostics du rejeu

Quand le rejeu est actif, la reponse de `/research/deep` porte
`diagnostics.replay` : `{mode, served, missed, recorded}` (somme de la
recherche, de l'extraction et du plan). Un run avec `missed` > 0 n'est pas un rejeu
fidele : le pipeline avale les erreurs de collecte, ce compteur est le seul
signal visible dans la reponse.

## Mesurer un changement de collecte

Un changement de collecte (filtre de resultats, replis, requetes generees,
choix des URLs) fait demander des requetes ou des pages absentes de
l'enregistrement : en `replay`, elles manquent. On le mesure en `fill` :

1. Copier le repertoire enregistre (`cp -r collecte-X fill-X`).
2. Un run en `fill` sur la copie, configuration B : seules les requetes
   nouvelles partent sur le web.
3. Les repetitions suivantes de B en `replay` sur la copie completee.
4. Comparer aux rejeux de A sur l'enregistrement d'origine, avec `ecart`.

Le web du jour du complement n'est pas celui de l'enregistrement : seule la
part complementee en souffre, et `recorded` dit combien elle pese.

## Limite : le temps n'est pas rejoue

Une collecte rejouee est instantanee. En direct, une section qui depasse son
budget de temps sort vide ; en rejeu, la meme section a le temps de chercher
et demande des requetes jamais enregistrees (`missed` > 0, a l'identique
d'un rejeu a l'autre). Constate sur `d08` le 13/11/2026, ou la cause etait
un budget consomme en file d'attente (C97, corrige). Un `missed` non nul
reste a expliquer avant de conclure sur la tache concernee.

## Branchement

Le rejeu est branche dans `create_orchestrator` (`app/api/v1/endpoints/research_deep.py`) : il suffit de poser
`WEBTOOLS_REPLAY_MODE` (`record`, `replay` ou `fill`) et `WEBTOOLS_REPLAY_DIR` dans l'environnement du service.
Le bilan (`served`, `missed`, `recorded`) est rendu dans `diagnostics.replay` de chaque reponse.
