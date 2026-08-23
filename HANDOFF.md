# Passation → Claude Code

Ce document est le point de reprise. Lis-le en entier avant de toucher au code,
puis lis `CLAUDE.md` pour les invariants — ils ne sont pas négociables et
plusieurs sont contre-intuitifs.

## Avertissement principal

**Rien de ce code n'a jamais été exécuté.** Il a été écrit en une session de
conception, la syntaxe est validée, la logique ne l'est pas. Les dépendances
n'ont pas été installées, aucun appel API n'a été fait, aucun sandbox n'a
tourné. Traite-le comme une architecture posée, pas comme un logiciel qui
marche. La liste de bugs plus bas n'est pas exhaustive.

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
| `kernel/guard.py` | écrit, non testé. Heuristiques à calibrer. |
| `kernel/archive.py` | écrit, non testé. Schéma SQLite complet. |
| `orchestrator/loop.py` | écrit, non testé. **Contient le bug bloquant n°1.** |
| `orchestrator/agent.py` | agent graine, volontairement naïf — ne pas améliorer à la main |
| `orchestrator/llm.py` | client OpenRouter, non testé |
| `orchestrator/sandbox.py` | client E2B, **surface d'API non vérifiée** |
| `orchestrator/backlog.py` | écrit, non testé |
| `orchestrator/directions.py` | écrit, non testé |
| `orchestrator/prompts/` | `ideator.md` + `proposer.md`, jamais évalués sur un vrai modèle |
| `api/main.py` | écrit, non testé. **Bug bloquant n°1 ici aussi.** |
| `api/static/index.html` | UI mobile, mise en page seulement — jamais branchée |
| `mcp/server.py` | écrit, non testé |
| `harness/` | 3 tâches avec fuzz tests. Difficulté réelle inconnue. |

## Bugs connus, par ordre de blocage

### 1. L'API et le worker ne partagent pas de mémoire — BLOQUANT

`api/main.py` référence `_loop.paused` et `_loop.human_verdict()`, mais l'API
et le worker sont **deux conteneurs distincts** dans `docker-compose.yml`.
`_loop` vaut `None` côté API : tout appel à `/verdict` ou `/control` plante.

Correction attendue : sortir l'état de contrôle en base. Ajoute une table
`control(key, value)` dans `kernel/archive.py`, l'API écrit
`paused/stop/verdict`, le worker la relit à chaque itération de boucle. Le
worker devient la seule chose qui exécute ; l'API ne fait qu'écrire des
intentions. Ne réponds pas à ce problème en fusionnant les deux services : la
séparation web / worker est ce qui permet le scale-to-zero en cloud.

### 2. Les workspaces ne sont pas des dépôts git — BLOQUANT

`backlog.apply_patch()` fait `git apply` dans `workspaces/A|B`, mais `Loop.__init__`
crée ces dossiers par `shutil.copytree` sans `git init`. Le patch échouera.

### 3. La clé OpenRouter n'entre pas dans le sandbox — BLOQUANT

`orchestrator/agent.py` lit `OPENROUTER_API_KEY`, mais `sandbox.Runner.evaluate()`
ne passe aucune variable d'environnement au sandbox E2B. L'agent évalué ne
pourra appeler aucun modèle. Passe la clé à la création du sandbox — et pense
au fait que tu injectes un secret dans un environnement qui exécute du code
généré : plafond bas sur cette clé, distincte de celle de l'orchestrateur.

### 4. Surface d'API E2B non vérifiée

`Sandbox(template=...)`, `files.write()` avec des bytes, `commands.run(on_stdout=)` :
tout ça vient de la mémoire, pas de la doc. Vérifie contre le SDK réel avant
de débugger le reste. Le template `base` n'a probablement ni `pytest` ni
`httpx` — il faut soit les installer au démarrage, soit construire un template
dédié.

### 5. `bl.close()` cible la mauvaise génération

Dans `Loop.human_verdict()`, `self.archive.recent(...)[0]` prend la plus
récente, pas celle dont on valide le verdict. Ajoute un `get(gen_id)` à
l'archive et utilise-le.

### 6. Le coût sandbox n'est jamais compté

`sandbox._estimate()` renvoie `0.0`. Le plafond `max_usd` ne couvre donc que
les tokens LLM. À brancher sur la durée réelle × tarif vCPU.

### 7. `guard.SUSPICIOUS` va produire beaucoup de faux positifs

Le motif `subprocess|os\.system|eval\(|exec\(` déclenchera sur des
améliorations parfaitement légitimes de l'agent. C'est délibéré — un faux
positif coûte un tour, un faux négatif coûte l'expérience. Mais si tout remonte
en revue humaine, calibre. Ne supprime pas le motif : resserre-le.

### 8. `/control` avec `rollback` renvoie 501

À brancher sur `git revert` de la génération visée.

## Ordre de travail proposé

1. **Faire tourner le harness seul, sans agents.** Écris un `solve()` bidon qui
   copie une solution correcte écrite à la main, et vérifie que
   `harness/run.py` sort bien son JSON avec `pass_rate: 1.0`. Ça valide le
   harness indépendamment de tout le reste.
2. Corriger les bugs 2, 3, 4 et faire tourner **une seule évaluation** de
   l'agent graine en sandbox. Objectif : un `pass_rate` entre **0.33 et 0.66**.
   Si c'est 1.00 les tâches sont trop faciles et l'expérience n'a plus d'objet ;
   si c'est 0.00 quelque chose est cassé. Ne va pas plus loin avant.
3. Corriger le bug 1 (état de contrôle en base), puis brancher l'UI et vérifier
   que `/state`, `/stream` et `/verdict` fonctionnent de bout en bout.
4. Faire tourner **un tour complet** : idéation → 2-4 tâches dans le backlog →
   diff → sandbox → verdict. Lis les tâches produites : citent-elles de vrais
   échecs, ou est-ce du générique type « ajouter de la gestion d'erreur » ? Si
   c'est générique, le problème est dans `prompts/ideator.md` ou dans le choix
   du modèle — corrige là, pas ailleurs.
5. Bugs 5, 6, 7, 8.
6. Compléter l'UI : directions, backlog, pause / reprise, taux d'acceptation.
   **Chaque ajout d'UI impose l'endpoint et l'outil MCP correspondants dans le
   même commit** — voir la table de parité dans `CLAUDE.md`, quatre lignes y
   sont encore marquées « à faire ».

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
à ce seul dépôt, et fais-le pousser sur une branche `evolution/*`, jamais sur
`main`.

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
