# Passation → Claude Code

Ce document est le point de reprise. Lis-le en entier avant de toucher au code,
puis lis `CLAUDE.md` pour les invariants — ils ne sont pas négociables et
plusieurs sont contre-intuitifs.

## État au 2026-08-23 (session Claude Code n°1)

Le code de conception a été importé dans le dépôt, puis **exécuté pour la
première fois**. L'avertissement d'origine (« rien de ce code n'a jamais
tourné ») ne vaut plus pour ce qui est listé ci-dessous, et vaut toujours
intégralement pour le reste.

**Ce qui tourne vraiment, vérifié :**

- le harness, seul, sans agents → `pass_rate: 1.0` avec des solutions écrites
  à la main, `0.0` avec un agent qui lève (étape 1 de l'ordre de travail) ;
- l'API : tous les endpoints, sur un serveur réel ;
- l'UI : chargée dans Chromium en 390×844 contre cette API, panneau, onglets,
  ajout et arbitrage de direction, pause / reprise, aller-retour HTTP complet ;
- le serveur MCP : charge et expose ses huit outils ;
- 73 tests, dont la parité UI ↔ MCP ↔ HTTP de l'invariant 6.

**Ce qui n'a toujours jamais tourné, faute de clés d'API :**

- tout appel à OpenRouter — `orchestrator/llm.py`, `orchestrator/agent.py` ;
- tout démarrage de sandbox E2B — `orchestrator/sandbox.py` ;
- donc aucune génération réelle, aucun tour complet, aucun `pass_rate` d'agent.

Ce sont exactement les **étapes 2 et 4** de l'ordre de travail. Elles restent
à faire, dans cet ordre, et rien ne devrait avancer avant l'étape 2.

## Ce que c'est

Deux agents identiques (A et B) s'améliorent mutuellement sur un objectif
mesurable. A patche B, B patche A, jamais soi-même. Un harness fixe donne le
score. L'humain arbitre par oui / non, il ne note pas la qualité.

Trois niveaux de décision :

| Niveau | Fichier | Auteur |
|---|---|---|
| Mission | `mission/OBJECTIVE.md` | humain, gelé pendant un run |
| Grandes idées | `mission/DIRECTIONS.yaml` | humain surtout ; l'IA propose, l'humain valide |
| Tâches | `mission/BACKLOG.yaml` | IA seule, en continu |

Un tour = idéation (l'agent écrit ses propres tâches à partir des échecs
observés) puis implémentation (un diff unique sur l'item le mieux classé).

## État par fichier

| Fichier | État |
|---|---|
| `kernel/guard.py` | corrigé (bug 7), 17 tests. Heuristiques resserrées, pas supprimées. |
| `kernel/archive.py` | table `control` ajoutée, `get(gen_id)` et `awaiting()` ajoutés. Testé. |
| `orchestrator/loop.py` | réécrit : plan de contrôle en base, résilience par tour. Testé sans LLM. |
| `orchestrator/agent.py` | agent graine, volontairement naïf — ne pas améliorer à la main. **Jamais exécuté.** |
| `orchestrator/llm.py` | inchangé. **Jamais exécuté** — le calcul du coût depuis `usage.cost` reste à vérifier. |
| `orchestrator/sandbox.py` | réécrit contre le SDK E2B réel. **Jamais exécuté contre un vrai sandbox.** |
| `orchestrator/backlog.py` | `apply_patch` ne lève plus, `promote`/`rollback` testés sur un vrai dépôt. |
| `orchestrator/directions.py` | inchangé, exercé par l'UI et l'API. |
| `orchestrator/prompts/` | inchangés. **Jamais évalués sur un vrai modèle** — c'est l'étape 4. |
| `api/main.py` | réécrit : n'exécute plus rien, dépose des intentions. Testé. |
| `api/static/index.html` | complété (panneau backlog / directions / contrôle), branché et vérifié en navigateur. |
| `mcp/server.py` | porté sur le SDK MCP 2.0 (`MCPServer`). Charge, huit outils. |
| `harness/` | 3 tâches, validées solvables. **Difficulté réelle face à un modèle : toujours inconnue.** |
| `tests/` | 73 tests. Hors `guard.MUTABLE` : les agents ne peuvent pas les patcher. |

## Bugs de la passation — état

| # | Sujet | État |
|---|---|---|
| 1 | API et worker sans mémoire partagée | **corrigé** — table `control(key,value)`, l'API écrit, le worker exécute |
| 2 | Workspaces pas des dépôts git | **n'était pas un bug** — `git apply` ne réclame aucun dépôt (vérifié). Le vrai blocage était voisin, voir ci-dessous |
| 3 | La clé OpenRouter n'entre pas dans le sandbox | **corrigé** — `AGENT_OPENROUTER_API_KEY` via `create(envs=…)`, sans repli sur la clé de l'orchestrateur |
| 4 | Surface E2B non vérifiée | **corrigé** — voir plus bas, deux hypothèses étaient fausses |
| 5 | `bl.close()` visait la mauvaise génération | **corrigé** — `archive.get(gen_id)`, avec test de régression |
| 6 | Coût sandbox jamais compté | **corrigé** — durée réelle × `sandbox_usd_per_hour`, facturé même en cas de crash |
| 7 | `SUSPICIOUS` trop large | **corrigé** — resserré, pas supprimé ; 6 faux positifs documentés par des tests |
| 8 | `/control rollback` renvoyait 501 | **corrigé** — `git revert` puis push, jamais `reset` |

### Ce que la vérification de la surface E2B a donné

Deux hypothèses de la passation étaient fausses, et une troisième manquait :

- `Sandbox(template=…)` **n'existe pas** : le constructeur sert à se rattacher
  à un sandbox existant. C'est `Sandbox.create(template=…, envs=…)` qui démarre.
- `commands.run()` **lève** `CommandExitException` sur code de retour non nul.
  Le rattrapage `json.JSONDecodeError` prévu ne pouvait jamais s'exécuter : un
  harness qui plante tuait le worker.
- `files.write(path, bytes)` et `on_stdout=` étaient corrects.
- Le template `base` n'a effectivement ni `pytest` ni `httpx` : un bootstrap
  les installe au démarrage. Un template dédié économiserait ~20 s par éval —
  `sandbox_bootstrap: ""` dans `config.yaml` une fois qu'il existe.

### Bugs trouvés en plus, non listés dans la passation

1. **`apply_patch` tuait le worker.** `check=True` faisait remonter une
   `CalledProcessError` jusqu'à `run()`, qui ne rattrape que `Budget`. Or un
   diff mal formé est le cas *normal* avec un modèle milieu de gamme. Renvoie
   désormais `(ok, stderr)`, et le message de git repart en contexte.
2. **Un tour raté tuait le worker.** Même schéma pour `chat_json` quand le
   modèle rate le JSON strict deux fois. `CLAUDE.md` dit qu'on doit marquer la
   génération `failed` — encore faut-il que le worker survive pour l'écrire.
   Trois tours ratés d'affilée arrêtent proprement, avec la raison dans le fil.
3. **`mcp/server.py` ne pouvait pas démarrer.** `mcp.server.fastmcp` a disparu
   du SDK 2.0 (`FastMCP` → `MCPServer`), et `python -m mcp.server` lance le SDK
   et non ce fichier — un paquet régulier l'emporte toujours sur un
   paquet-espace-de-noms. Se lance par `python mcp/server.py`.
4. **Les workspaces ne couvraient qu'`orchestrator/`** alors que `guard.MUTABLE`
   autorise aussi `api/` et `mcp/` : un patch parfaitement légitime y était
   accepté par le garde-fou, puis inapplicable faute de fichier cible.
5. **`harness/manifest.yaml` listait `reverse_words`**, une tâche inexistante,
   et ignorait les trois vraies. Un test verrouille désormais le miroir.
6. **`subprocess.TimeoutExpired` n'était pas rattrapée** dans `harness/run.py` :
   une tâche partie en boucle emportait toute l'évaluation.

### Deux décisions prises qui méritent ton avis

- **La boucle attend tant qu'un arbitrage humain est en attente.** Sinon A et B
  continuent de patcher un code que tu n'as pas laissé avancer : doublons
  garantis et budget brûlé. C'est un changement de comportement, pas une
  correction de bug.
- **Deux gates, deux statuts.** `awaiting_gate` (autoriser l'évaluation d'un
  patch jugé sensible) et `awaiting_human` (intégrer un patch déjà mesuré).
  « Oui » ne voulait pas dire la même chose dans les deux cas, et le code
  d'origine les confondait — un `ok` sur un patch jamais évalué aurait promu
  un candidat inexistant.

## Ce qui reste, dans l'ordre

1. **Étape 2 — une seule évaluation de l'agent graine en sandbox.** Il faut
   `E2B_API_KEY` et `AGENT_OPENROUTER_API_KEY`. Objectif : un `pass_rate` entre
   **0.33 et 0.66**. Si c'est 1.00 les tâches sont trop faciles et l'expérience
   n'a plus d'objet ; si c'est 0.00 quelque chose est cassé. Ne va pas plus loin
   avant.

   Note utile pour lire le résultat : les trois tâches ont été résolues à la
   main pour valider le harness, et `rle` est nettement la plus dure — un
   encodage naïf est ambigu dès qu'un chiffre apparaît dans l'entrée, il faut
   l'échapper. Un agent graine qui rendrait 0.66 en ratant `rle` serait
   exactement dans la fenêtre visée.

2. **Étape 4 — un tour complet** : idéation → 2-4 tâches dans le backlog →
   diff → sandbox → verdict. Lis les tâches produites : citent-elles de vrais
   échecs, ou est-ce du générique type « ajouter de la gestion d'erreur » ? Si
   c'est générique, le problème est dans `prompts/ideator.md` ou dans le choix
   du modèle — corrige là, pas ailleurs.

3. **`tokens_per_task` n'est jamais mesuré** : `harness/run.py` renvoie `0.0`
   en dur. C'est une métrique secondaire de `OBJECTIVE.md` et la cible de
   DIR-002 — tant qu'elle vaut zéro, aucune idée de réduction de coût n'est
   falsifiable. À brancher avant d'attendre quoi que ce soit de DIR-002.

4. **Le coût LLM vient de `usage.cost` d'OpenRouter**, jamais vérifié contre
   une vraie réponse. Si le champ est absent, `max_usd` ne protège plus rien :
   à contrôler au premier appel réel.

## Déploiement : Kubernetes, archive en Postgres

`docker-compose.yml` montait `./data` dans les deux services : l'API et le
worker partageaient le fichier SQLite, et c'est ce qui faisait fonctionner le
plan de contrôle. Le bind mount local le donne gratuitement, le cluster non —
le stockage bloc est mono-attachement (un volume Railway se monte sur un seul
service, un volume Fly est lié à une seule machine, un PVC `ReadWriteOnce`
n'est partageable entre deux pods que s'ils sont sur le même nœud).

**Tranché : Postgres.** `kernel/archive.py` a désormais deux backends —
SQLite quand l'adresse est un chemin, Postgres quand c'est une DSN. Un seul
réglage (`CROSSPATCH_ARCHIVE`), aucune branche ailleurs dans le code. Le SQL
est écrit une fois en dialecte SQLite et traduit ; `tests/test_archive.py`
exécute le **même contrat** sur les deux backends, et la CI démarre un
Postgres pour que ça reste vrai.

Ce que la mise en place a fait tomber, et qui n'était pas prévu :

- **`json_extract` n'existe pas en Postgres.** `best()` et `plateau()`
  l'utilisent : ce sont les deux requêtes qui cassaient en premier.
- **`lastrowid` n'existe pas non plus** — remplacé par `RETURNING id`.
- **psycopg ouvre une transaction au premier `SELECT` et ne la referme
  jamais.** L'API garde sa connexion ouverte : elle serait restée « idle in
  transaction » à vie, bloquant tout DDL et empêchant le VACUUM de nettoyer.
  Trouvé parce que la suite de tests s'est bloquée sur un `DROP TABLE`.
  Corrigé par `autocommit=True`.
- **`SCHEMA.format()` mangeait les accolades des commentaires SQL**
  (`-- json {pass_rate, …}`). Substitution par marqueurs explicites.

`docker-compose.yml` tourne maintenant sur Postgres lui aussi : une
divergence local / cluster est exactement la classe de bug que ce projet ne
peut pas se permettre.

`deploy/k8s/` contient les manifests complets, avec leur mode d'emploi dans
`deploy/k8s/README.md`. Trois invariants y sont verrouillés par des tests :
`worker` en `replicas: 1` + `Recreate` (deux workers proposeraient chacun une
génération sur le même état), les workspaces et candidats sur volume
persistant (les perdre remet A et B à la graine), et l'uid 10001 identique
entre l'image et les manifests.

**L'image n'a pas pu être construite ici** : le registre Docker est bloqué
par le proxy réseau de la session. Le `Dockerfile` est écrit et relu, il
n'est pas vérifié. Un `docker build -t crosspatch:dev .` est la première
chose à faire, et la seule étape de ce lot qui reste non exécutée.

## L'UI responsive est un travail humain, pas une tâche d'agent

À dire explicitement parce que la tentation reviendra : rendre l'UI complète et
responsive relève de l'étape 6, faite par un humain et Claude Code. **Ce n'est
pas une direction pour les agents.**

Raison : aucune métrique du harness ne bouge quand une UI devient responsive.
Des agents lâchés là-dessus passeraient des générations sur du CSS et
s'auto-déclareraient satisfaits, sans aucun moyen de vérifier. C'est exactement
le mode d'échec que toute l'architecture cherche à éviter.

La part qui *est* légitimement du ressort des agents est couverte par DIR-003 :
structurer ce qu'un agent transmet à l'autre (`observations`, `evidence`,
`hypothesis`, `prediction`) plutôt que du texte libre. Un format structuré se
parse mieux par l'agent suivant *et* s'affiche mieux dans l'UI — et se mesure
via `acceptance_rate`. Si une tâche du backlog parle de mise en page, de
couleurs ou de points de rupture, elle est hors périmètre : ferme-la.

## Pièges qui ne sont pas des bugs

Ces choix sont délibérés et vont sembler faux. Ne les « corrige » pas.

- Le score retenu est le **pire** des N runs du soak, pas la moyenne.
- Le modèle des agents est **volontairement milieu de gamme**. Passer à un
  modèle plus fort améliorerait les scores et supprimerait l'expérience.
- L'agent graine (`orchestrator/agent.py`) est **volontairement naïf**.
- `BACKLOG.yaml` part **vide**. Ne le seede pas : ce que les agents trouvent,
  ou ne trouvent pas, est le résultat.
- La convergence par doublons (75 % d'idées déjà connues sur 3 tours) est un
  **résultat**, pas une panne. Ne monte pas la température pour la contourner.
- Aucun LLM ne juge un autre LLM. Si tu te surprends à ajouter un
  LLM-as-judge dans la boucle, relis `CLAUDE.md`.

## Avant le premier push public

```bash
git status --short | grep -E '\.env$|\.db$' && echo "NE PAS POUSSER"
```

`.gitignore` couvre `.env`, `data/`, `workspaces/`, `candidates/`, `*.db`.
Le worker fait `git push` après validation : utilise un PAT fine-grained limité
à ce seul dépôt. Il pousse sur `evolution/<run_id>`, jamais sur `main` — c'est
câblé dans `backlog.promote()` et vérifié par un test.

## Choix du langage — Python maintenant, TS peut-être plus tard

Question déjà tranchée, mais qui reviendra : pourquoi pas du TypeScript,
puisqu'il y a une UI web ?

Le projet a deux zones, et elles n'ont pas la même réponse.

- **Control plane** (`api/`, `mcp/`, l'UI) : TypeScript serait objectivement
  meilleur. Un seul langage front et back, types partagés, un seul build.
- **Zone mesurée** (`harness/`, `orchestrator/agent.py`) : Python garde
  l'avantage. `pytest` + fuzz + coverage n'a pas d'équivalent aussi solide, et
  toute la littérature de référence (SWE-bench, DGM, Polyglot) est en Python.
  Le jour où on branche un vrai benchmark à la place des trois tâches maison,
  il sera en Python.

Le meilleur argument pour TS, à garder en tête : `tsc --noEmit` comme portail
avant le sandbox rejetterait un patch non compilable en 200 ms, sans démarrer
de microVM ni brûler de tokens. Python n'a rien d'aussi net. Si le coût par
génération devient un problème, c'est une piste sérieuse.

**Ce qui a décidé** : le langage d'`agent.py` n'est pas un choix technique,
c'est une **variable expérimentale**. C'est le fichier que A et B se
réécrivent, donc sa langue détermine ce qu'on observe. Un modèle milieu de
gamme — choisi exprès pour le headroom — écrit du Python nettement mieux que
du TypeScript. En TS, on risquerait de mesurer « le modèle peine sur les
génériques » au lieu de « la boucle d'amélioration fonctionne ». Le signal se
pollue.

**Si la question revient** : ne réécris pas tout. La frontière est déjà propre
— l'API est purement HTTP, l'UI est un fichier autonome, `mcp/server.py` ne
fait que du proxy. Ces trois-là se portent en TS sans toucher à la boucle ni
au harness. L'inverse est bien pire : réécrire le harness ou l'agent une fois
qu'il existe des générations archivées fait perdre la comparabilité de tout ce
qui précède. Le harness et `agent.py` restent en Python, sauf décision
explicite de démarrer un nouveau `run_id` en repartant de zéro.

## Questions ouvertes, à trancher plus tard

- Faut-il rejouer périodiquement les anciennes générations pour détecter une
  dérive du modèle côté fournisseur ?
- Le soak à 3 runs suffit-il à distinguer un vrai gain du bruit ? Une mesure de
  la variance sur la génération de référence donnerait la réponse.
- Garder plusieurs branches vivantes dans l'archive (à la Huxley-Gödel Machine)
  plutôt qu'une seule lignée — ça change la structure de `Archive`.
