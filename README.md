# crosspatch

> Deux agents identiques qui s'améliorent l'un l'autre.
> Aucun n'a le droit de se patcher lui-même.

A lit le code de B et propose un correctif ; B fait de même pour A. Un harness
fixe donne le score — jamais un agent. L'humain arbitre par oui / non, il ne
note pas la qualité.

Projet de recherche, inspiré de la Darwin Gödel Machine et de la Huxley-Gödel
Machine, avec deux différences : la discussion entre agents est en langage
naturel et auditable, et les agents génèrent eux-mêmes leur backlog de tâches
à partir des échecs observés.

## Démarrer

```bash
cp .env.example .env        # OPENROUTER_API_KEY, AGENT_OPENROUTER_API_KEY, E2B_API_KEY
openssl rand -hex 32        # → CONTROL_TOKEN
docker compose up
```

## Secrets

Le dépôt est public : rien ne va dans git, et un test le vérifie
(`tests/test_secrets.py`). Cinq valeurs, et **trois destinations qui ne sont
pas interchangeables** :

| Où | Quoi | Pourquoi là |
|---|---|---|
| `.env` local | les six | lu par `docker compose`, ignoré par git |
| Secret Kubernetes | les six | voir `deploy/k8s/README.md` — créé en ligne de commande, jamais dans git |
| Secrets GitHub Actions | rien | la CI ne fait aucun appel réseau, elle n'en a besoin d'aucun |

Le dernier point mérite d'être dit : les « secrets de dépôt » GitHub ne sont
lus que par les workflows Actions. Les y poser ne donne rien à l'application —
c'est l'orchestrateur de conteneurs qui l'alimente.

Deux clés OpenRouter, pas une : celle de l'orchestrateur, et celle de l'agent
évalué, injectée dans le sandbox. Cette seconde clé vit dans un environnement
qui exécute du code écrit par un modèle — plafond bas, et jamais la même.

`GIT_PUSH_TOKEN` est le plus sensible pour un dépôt public : il autorise le
worker à pousser. PAT fine-grained, ce dépôt uniquement, `Contents: write`.
Il passe par `GIT_ASKPASS`, donc jamais dans `argv` ni dans `.git/config`, et
les erreurs de push sont expurgées avant d'entrer dans le fil.

## Vérifier

```bash
pip install -r requirements.txt pytest
pytest -q                   # 73 tests, aucun n'appelle d'API externe
```

`tests/test_harness.py` rejoue le harness avec des solutions écrites à la main
et vérifie qu'il sort `pass_rate: 1.0`. Si celui-là échoue, aucune mesure de
génération n'a de sens et il est inutile de débugger le reste.

UI sur http://localhost:8080 — le fil de discussion et les boutons de validation.

## Kubernetes

Manifests complets dans `deploy/k8s/`, mode d'emploi dans
`deploy/k8s/README.md` :

```bash
docker build -t crosspatch:dev .
kubectl apply -k deploy/k8s
```

Trois pods : `postgres` (l'archive), `api` et `worker`. L'archive est en
Postgres et non dans un volume partagé, parce que deux pods ne peuvent pas
monter un PVC `ReadWriteOnce` de façon fiable. Le sandbox reste délégué à
E2B : le code path est identique en local et en cluster.

## Piloter depuis Claude Code

```json
{ "mcpServers": { "crosspatch": {
    "command": "python", "args": ["mcp/server.py"],
    "env": { "CROSSPATCH_API": "https://<ton-app>", "CONTROL_TOKEN": "..." }
}}}
```

Outils exposés : `get_state`, `get_backlog`, `get_directions`,
`add_direction`, `set_direction_state`, `get_transcript`, `set_verdict`,
`control`.

`python mcp/server.py`, pas `python -m mcp.server` : ce dossier porte le même
nom que le SDK, et `-m` lancerait le SDK.

## L'invariant qui compte

Le processus qui tourne ne se modifie **jamais** lui-même. Il produit un
candidat, le fait évaluer en sandbox, et si tu valides, le candidat part en
git — le redéploiement recharge le code. Pas de hot-patch : c'est ce qui
garantit qu'il existe toujours un point de retour.

`kernel/`, `harness/` et `mission/OBJECTIVE.md` sont hors d'atteinte des
agents, et leur empreinte est vérifiée à chaque génération.

## Avertissement

Ce projet exécute du code généré par modèle de langage. L'exécution se fait
dans des sandboxes E2B isolés (microVM), jamais sur l'hôte, et les chemins
`kernel/`, `harness/` et `mission/OBJECTIVE.md` sont hors d'atteinte des
agents. Ces précautions réduisent le risque sans l'annuler : ne pointe pas
ce système vers un dépôt ou une infrastructure qui compte, et garde un
plafond de dépense actif (`max_usd` dans `config.yaml`).

## Licence

MIT — voir `LICENSE`.
