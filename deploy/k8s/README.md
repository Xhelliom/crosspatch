# Déploiement sur un cluster local

Trois pods : `postgres` (l'archive), `api` (ce que le téléphone interroge) et
`worker` (la seule chose qui exécute). L'API et le worker ne partagent aucune
mémoire — l'API dépose des intentions dans la table `control`, le worker les
relit à chaque tour de boucle. C'est précisément pour ça que l'archive est
en Postgres et pas dans un volume partagé : deux pods ne peuvent pas monter
un PVC `ReadWriteOnce` de façon fiable dès que le cluster a plus d'un nœud.

## 1. L'image

Elle est construite et publiée par GitHub Actions
(`.github/workflows/images.yml`) sur `ghcr.io/xhelliom/crosspatch`, à chaque
push sur `main` **et sur `evolution/**`** — cette seconde branche est celle
que le worker pousse après ton « oui », donc c'est elle qui referme la
boucle « validation → redéploiement ». Aucune image n'est publiée sans que
la suite de tests soit passée.

Un paquet GHCR est **privé par défaut**, même sur un dépôt public. Deux
options :

- le rendre public : page du paquet → *Package settings* → *Change
  visibility* ; le cluster tire sans identifiants ;
- le garder privé et créer un secret de pull :

```bash
kubectl -n crosspatch create secret docker-registry ghcr \
  --docker-server=ghcr.io --docker-username=<utilisateur> \
  --docker-password=<PAT avec read:packages>
```
(puis ajouter `imagePullSecrets: [{name: ghcr}]` aux deux Deployments).

### Ou en local, sans registre

```bash
docker build -t crosspatch:dev .
docker save crosspatch:dev | sudo k3s ctr images import -   # k3s / k3d
# kind load docker-image crosspatch:dev
# minikube image load crosspatch:dev
```

Puis pointe les manifests dessus — et repasse la politique de pull à
`IfNotPresent`, sinon le cluster cherchera l'image dans un registre :

```yaml
# deploy/k8s/kustomization.yaml
images:
  - name: ghcr.io/xhelliom/crosspatch
    newName: crosspatch
    newTag: dev
patches:
  - target: { kind: Deployment }
    patch: |
      - op: replace
        path: /spec/template/spec/containers/0/imagePullPolicy
        value: IfNotPresent
```

## 2. Créer le Secret

Jamais dans git. En ligne de commande, une seule fois :

```bash
kubectl create namespace crosspatch

kubectl -n crosspatch create secret generic crosspatch-secrets \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=CONTROL_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=OPENROUTER_API_KEY='sk-or-…' \
  --from-literal=AGENT_OPENROUTER_API_KEY='sk-or-…' \
  --from-literal=E2B_API_KEY='e2b_…' \
  --from-literal=GIT_PUSH_TOKEN='github_pat_…'
```

`AGENT_OPENROUTER_API_KEY` doit être **distincte** d'`OPENROUTER_API_KEY` et
plafonnée bas : elle est injectée dans une VM qui exécute du code écrit par un
modèle. `GIT_PUSH_TOKEN` est un PAT fine-grained limité à ce dépôt, permission
`Contents: write` — le worker s'en sert pour pousser `evolution/<run_id>`
après ton « oui ». Laisse-le vide tant que tu ne veux pas qu'il pousse.

Note le `CONTROL_TOKEN` : c'est lui que l'UI demande au premier chargement.

```bash
kubectl -n crosspatch get secret crosspatch-secrets \
  -o jsonpath='{.data.CONTROL_TOKEN}' | base64 -d; echo
```

## 3. Déployer

```bash
kubectl apply -k deploy/k8s
kubectl -n crosspatch rollout status deploy/api deploy/worker
```

`config.yaml` est généré depuis le fichier du dépôt par kustomize : changer
`max_usd` ou `model` puis réappliquer suffit, le suffixe de hachage force le
redémarrage. Pas besoin de reconstruire l'image.

## 4. Y accéder depuis le téléphone

Le Service est en `ClusterIP` — rien n'est exposé par défaut. Au choix :

```bash
# le plus simple, depuis la machine qui porte le cluster
kubectl -n crosspatch port-forward --address 0.0.0.0 svc/api 8480:8080
```

Puis `http://<ip-de-la-machine>:8480` depuis le téléphone. Pour quelque chose
de durable, un Ingress ou Tailscale — mais garde en tête que le
`CONTROL_TOKEN` est la seule chose qui protège l'API : ne l'expose pas sur
Internet sans autre couche devant.

## 5. Piloter depuis Claude Code

```json
{ "mcpServers": { "crosspatch": {
    "command": "python", "args": ["mcp/server.py"],
    "env": { "CROSSPATCH_API": "http://<ip>:8480", "CONTROL_TOKEN": "…" }
}}}
```

## Ce qu'il faut savoir avant de toucher aux manifests

- **`worker` est en `replicas: 1` et `strategy: Recreate`.** Deux workers
  proposeraient chacun une génération sur le même état et consommeraient les
  mêmes intentions. Un `RollingUpdate` en ferait cohabiter deux le temps d'un
  roulement — c'est pour ça qu'il est interdit ici.
- **Le PVC `worker-state` porte les workspaces et les candidats.** Perdre les
  workspaces remet A et B à la graine, donc efface l'expérience en cours.
  Perdre les candidats casse toute promotion dont le verdict arrive après un
  redémarrage.
- **La racine du worker est inscriptible**, contrairement à celle de l'API :
  `promote()` recopie le candidat dans `/app`, y fait un commit git, puis
  pousse. L'image embarque `.git` pour ça.
- `tests/test_manifests.py` valide ces fichiers contre les modèles d'API
  Kubernetes réels et vérifie ces invariants. Lance `pytest -q` avant
  d'appliquer.
