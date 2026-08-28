# crosspatch

Deux agents identiques qui s'améliorent mutuellement sur un objectif mesurable.
Un humain arbitre par oui / non, il ne note pas la qualité.

## Invariants — à ne jamais casser

1. **Le processus vivant ne se patche jamais lui-même.** Il produit un candidat,
   le fait évaluer en sandbox, et après validation humaine le pousse en git.
   Le redéploiement recharge le code. Pas de hot-patch, jamais.
2. **`kernel/`, `harness/` et `mission/OBJECTIVE.md` sont immuables** pour les
   agents. Leur empreinte SHA est vérifiée à chaque génération ; si elle bouge,
   la boucle s'arrête. Si tu modifies ces chemins toi-même, c'est normal — mais
   fais-le dans un commit explicite.
3. **Un agent ne modifie jamais son propre code**, seulement celui de l'autre.
   A patche B, B patche A. C'est ce qui casse l'auto-validation.
4. **Le harness est la seule source de vérité sur la qualité.** Aucun agent
   n'évalue un autre agent. Si tu te surprends à ajouter un LLM-as-judge dans
   la boucle, relis ce point.
5. **`OBJECTIVE.md` est gelé pendant un run.** Le modifier impose un nouveau
   `run_id`, sinon les générations ne sont plus comparables.
6. **Parité UI ↔ MCP.** Toute information lisible dans l'UI et toute action
   déclenchable depuis l'UI doivent l'être aussi via un outil MCP, et
   réciproquement. Les deux tapent sur les mêmes endpoints — l'UI est un
   client HTTP au même titre que `mcp/server.py`, jamais un chemin privilégié.
   Concrètement, si tu ajoutes un panneau ou un bouton dans
   `api/static/index.html`, tu ajoutes dans le même commit l'endpoint dans
   `api/main.py` et l'outil correspondant dans `mcp/server.py`. Une
   fonctionnalité qui n'existe que d'un côté est un bug : je pilote depuis le
   téléphone autant que depuis une session Claude Code, et je dois pouvoir
   passer de l'un à l'autre sans rien perdre.

## Les trois niveaux — qui décide quoi

| Niveau | Fichier | Auteur | Modifiable en cours de run |
|---|---|---|---|
| Mission | `mission/OBJECTIVE.md` | humain | non — gelé, sinon plus rien n'est comparable |
| Grandes idées | `mission/DIRECTIONS.yaml` | humain surtout ; l'IA propose, l'humain valide | oui — c'est le volant |
| Tâches | backlog, dans l'archive | IA seule | oui, en continu |

L'humain ouvre les terrains, l'IA les explore. Une tâche non rattachée à une
direction active est écartée à l'ingestion. Une direction proposée par un agent
reste en `input_required` jusqu'à validation : c'est la frontière entre force de
proposition et auto-autorisation.

`DIRECTIONS.yaml` est dans `PROTECTED` : les agents ne peuvent pas le patcher,
seule l'API l'écrit.

## Deux choses à ne pas confondre

- **`harness/tasks/`** = l'étalon de mesure. Écrit par l'humain, gelé pour la
  durée d'un run, immuable pour les agents. Sans mètre fixe, aucune génération
  n'est comparable. On n'ajoute pas de tâche en cours de run.
- **le backlog** = les idées d'amélioration. **Généré par les agents** en
  phase d'idéation. Ne jamais le seeder à la main : ce que les agents
  trouvent — ou ne trouvent pas — *est* le résultat de l'expérience.
  Il vit dans l'archive (`documents`, clé `BACKLOG.yaml`), pas dans un
  fichier : c'est un état que le worker écrit et que l'API sert, et les deux
  sont des conteneurs distincts. Tant qu'il était un fichier de `/app`,
  chacun lisait le sien — `/backlog` a servi le fichier vide de l'image
  pendant tous les premiers runs — et un `docker build` le remettait à zéro,
  ce qui vidait `known` et rendait la déduplication aveugle.

## Le tour, en deux phases

1. **Idéation** (`prompts/ideator.md`) — le proposeur lit le code de l'agent
   cible, les échecs réels de la dernière éval et les rejets passés, puis écrit
   2 à 4 hypothèses dans le backlog. Aucun code produit à cette phase.
2. **Implémentation** (`prompts/proposer.md`) — il prend l'item le mieux classé
   et produit un diff unique.

La déduplication est le capteur de convergence : quand A et B ne proposent plus
que des idées déjà connues sur 3 tours consécutifs, la boucle s'arrête. Deux
agents identiques qui n'ont plus rien de neuf à se dire, c'est le résultat.
Ne « répare » pas ça en relançant l'idéation avec une température plus haute.

## Architecture

```
kernel/        immuable — guard (chemins protégés), archive SQLite, budget
harness/       immuable — tâches et scoring, monté en lecture seule en sandbox
orchestrator/  mutable  — boucle, client OpenRouter, client E2B, backlog
api/           mutable  — FastAPI : /state /backlog /verdict /control /stream
mcp/           mutable  — serveur MCP, l'interface de cette session
mission/       OBJECTIVE.md (gelé) + DIRECTIONS.yaml (écrit par l'API)
               le backlog, lui, est dans l'archive — état partagé
```

Control plane en un conteneur, exécution déléguée à E2B. Même code path en
local (`docker compose up`) et en cloud (`git push`).

**L'API et le worker ne partagent aucune mémoire** : ce sont deux conteneurs.
L'API écrit des intentions dans la table `control` de l'archive
(`paused`, `stop`, `verdict:<gen>`, `rollback:<gen>`), le worker les relit à
chaque itération et reste seul à exécuter quoi que ce soit. Tout ce que les
deux doivent voir identique passe par l'archive — les intentions par
`control`, le backlog par `documents`. Rien par un fichier de `/app` : il y
en a deux copies, et aucune ne survit à un redéploiement. Ne pas répondre à
un problème de synchronisation en fusionnant les deux services : la séparation
web / worker est ce qui permet le scale-to-zero en cloud.

L'archive a donc **deux backends** : SQLite quand `archive_path` est un
chemin, Postgres quand c'est une DSN. SQLite est le défaut — c'est ce qui
fait tourner la suite sans dépendance. Postgres est ce qui tourne en
cluster, parce que deux pods ne peuvent pas partager un PVC de façon
fiable. Le SQL est écrit **une seule fois**, en dialecte SQLite, et traduit
(`_pour_postgres`) : toute requête ajoutée à `kernel/archive.py` doit
apparaître dans `tests/test_archive.py`, qui exécute le même contrat sur les
deux. `deploy/k8s/` est validé par `tests/test_manifests.py` contre les
modèles d'API Kubernetes réels.

## Conventions

- Python 3.12, `from __future__ import annotations`, type hints partout.
- Prose et commentaires en français, identifiants en anglais.
- États du backlog alignés sur A2A : `submitted`, `working`, `input_required`,
  `completed`, `failed`, `canceled`.
- **Le coupe-circuit est dans le bandeau, pas dans un panneau.** Suspendre
  est un garde-fou de dépense : il doit être atteignable en un tap et agir
  avant la prochaine dépense — pas au tour suivant. `_attendre_reprise()` est
  appelé avant chaque appel de modèle et avant chaque sandbox du soak. Si tu
  ajoutes une dépense ailleurs dans la boucle, ajoute l'appel avec.
- Deux gates humains, deux statuts distincts : `awaiting_gate` demande
  l'autorisation d'évaluer un patch que le garde-fou a jugé sensible,
  `awaiting_human` demande l'intégration d'un patch déjà mesuré. « Oui » n'y
  veut pas dire la même chose ; `/state` expose `stage` pour les distinguer.
- Le modèle des agents est **volontairement milieu de gamme**. Si tu proposes
  de passer à un modèle plus fort pour améliorer les scores, tu supprimes
  l'expérience : le headroom est le sujet.

## Surface exposée (garder les trois colonnes alignées)

| Fonction | HTTP | MCP | UI |
|---|---|---|---|
| État du run | `GET /state` | `get_state` | bandeau + rail |
| Taux d'acceptation | dans `GET /state` | dans `get_state` | bandeau + panneau › contrôle |
| Backlog | `GET /backlog` | `get_backlog` | panneau › backlog |
| Grandes idées | `GET /directions` | `get_directions` | panneau › directions |
| Poser une direction | `POST /directions` | `add_direction` | panneau › directions (formulaire) |
| Trancher une direction | `POST /directions/{id}/verdict` | `set_direction_state` | panneau › directions (boutons) |
| Fil de discussion | `GET /transcript`, `GET /stream` | `get_transcript` | fil principal |
| Verdict humain | `POST /verdict/{id}` | `set_verdict` | barre de décision |
| Pause / reprise / arrêt | `POST /control` | `control` | bandeau (coupe-circuit) + panneau › contrôle |
| Annuler une génération | `POST /control` (`rollback`) | `control` | panneau › contrôle |

Une ligne incomplète est une dette, pas une étape. `tests/test_parite.py`
rend l'invariant exécutable : il compare les chemins appelés par
`api/static/index.html` et par `mcp/server.py` aux routes déclarées dans
`api/main.py`, et échoue si une colonne prend du retard sur une autre.

`acceptance_rate` (part des propositions qui passent le harness sans régression)
est calculé dans `kernel/archive.py`, donc immuable. C'est la métrique de
DIR-003 : elle note la qualité de ce qu'un agent transmet à l'autre. Ne la
déplace pas dans `orchestrator/`.

## Debug

Ne compte pas sur un terminal — je pilote souvent depuis le téléphone.
Tout diagnostic passe par les outils MCP : `get_state`, `get_transcript`,
`get_backlog`. Si une info manque pour debug, ajoute-la à `/state` plutôt que
de créer un script à lancer à la main.

## Langage

Python pour tout, délibérément. Le control plane (`api/`, `mcp/`, l'UI) gagnerait
à être en TypeScript et pourra être porté plus tard — la frontière est propre,
l'API est purement HTTP. Mais `harness/` et `orchestrator/agent.py` restent en
Python : la langue de l'agent est une variable expérimentale, pas un choix de
confort, et un modèle milieu de gamme écrit mieux du Python. Voir `HANDOFF.md`
pour le raisonnement complet avant de rouvrir le sujet.

## Pièges connus

- `git apply` échoue silencieusement si le diff a des offsets de ligne faux —
  toujours `--unidiff-zero` et vérifier le code retour.
- Un modèle moyen rate parfois le JSON strict. Un seul retry, puis on marque
  la génération `failed` : c'est une donnée sur le modèle, pas un bug à masquer.
- Le score retenu est le **pire** des N runs du soak, pas la moyenne. Ne
  « corrige » pas ça en passant à la moyenne, c'est délibéré.
