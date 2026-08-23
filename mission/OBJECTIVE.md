# Mission

> Ce fichier est **gelé pendant un run**. Le modifier démarre un nouveau run
> (nouvelle lignée dans l'archive). C'est volontaire : deux générations ne sont
> comparables que si elles ont été évaluées contre le même objectif.

## run_id
`2026-08-23-001`

## Goal A — tâche externe (mesurable, prioritaire)

Améliorer le taux de réussite de l'agent sur le harness `harness/manifest.yaml`.

Métrique primaire : `pass_rate` (0.0 → 1.0)
Métriques secondaires : `tokens_per_task`, `wall_time_p50`

Un patch n'est retenu que s'il n'entraîne **aucune régression** sur `pass_rate`.

## Goal B — auto-amélioration de l'app (secondaire, subordonné à A)

Améliorer le code de `orchestrator/` lui-même : outils, gestion du contexte,
stratégie de patch, priorisation du backlog, ergonomie de l'UI.

**Règle de subordination** : une amélioration de type B ne se justifie *jamais*
par elle-même. Elle doit produire un effet mesurable sur les métriques de A,
constaté sur au moins 2 générations consécutives. Une proposition B qui ne
formule pas d'hypothèse falsifiable sur A est rejetée automatiquement.

## Hors périmètre (jamais modifiable — voir kernel/guard.py)

- `harness/` — les tâches et le scoring
- `kernel/` — budget, archive, gate humain, garde-fous
- `mission/OBJECTIVE.md` — ce fichier

Toute proposition touchant ces chemins est rejetée avant même d'être évaluée.

## Condition d'arrêt

- `pass_rate` en plateau (< +0.01) sur 5 générations consécutives, OU
- budget épuisé, OU
- arrêt manuel via l'UI.
