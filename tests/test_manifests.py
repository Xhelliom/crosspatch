"""
Les manifests de `deploy/k8s/` validés contre les vrais modèles d'API
Kubernetes, plus les invariants propres à ce projet.

La validation structurelle attrape les fautes de frappe silencieuses — un
`stategy:` ou un `readinessProb:` que l'API accepte sans broncher parce
qu'elle ignore les champs inconnus, et qui ne se voient qu'en production.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "deploy" / "k8s"


def _documents() -> list[tuple[str, dict]]:
    docs = []
    for f in sorted(K8S.glob("*.yaml")):
        if f.name in ("kustomization.yaml", "secret.example.yaml"):
            continue
        for d in yaml.safe_load_all(f.read_text()):
            if d:
                docs.append((f.name, d))
    return docs


def _par_genre(kind: str) -> list[dict]:
    return [d for _, d in _documents() if d["kind"] == kind]


def _nomme(kind: str, name: str) -> dict:
    for d in _par_genre(kind):
        if d["metadata"]["name"] == name:
            return d
    raise AssertionError(f"{kind}/{name} introuvable")


# --- validation structurelle contre les modèles d'API ---------------------

def _classe(api_version: str, kind: str):
    from kubernetes import client
    groupe = api_version.split("/")[0] if "/" in api_version else ""
    version = api_version.split("/")[-1]
    prefixe = {"": "V1", "apps": "V1", "batch": "V1"}.get(groupe)
    if prefixe is None:
        pytest.skip(f"groupe d'API non couvert : {api_version}")
    return getattr(client, f"{version.upper()}{kind}", None) or \
        getattr(client, f"V{version[1:]}{kind}", None)


def _camel_vers_snake(nom: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", nom).lower()


def _valider(noeud, klass, chemin: str, erreurs: list[str]) -> None:
    """Descend le document en vérifiant chaque clé contre `attribute_map`."""
    from kubernetes import client

    if not isinstance(noeud, dict) or klass is None:
        return
    connus = {v: k for k, v in klass.attribute_map.items()}   # camel -> snake
    types = klass.openapi_types
    for cle, valeur in noeud.items():
        if cle not in connus:
            erreurs.append(f"{chemin}.{cle} — champ inconnu de {klass.__name__}")
            continue
        t = types[connus[cle]]
        enfant = re.fullmatch(r"list\[(\w+)\]", t)
        if enfant:
            sous = getattr(client, enfant.group(1), None)
            for i, elem in enumerate(valeur or []):
                _valider(elem, sous, f"{chemin}.{cle}[{i}]", erreurs)
        else:
            _valider(valeur, getattr(client, t, None), f"{chemin}.{cle}", erreurs)


def test_les_manifests_valident_contre_les_modeles_kubernetes():
    pytest.importorskip("kubernetes")
    erreurs: list[str] = []
    for fichier, doc in _documents():
        klass = _classe(doc["apiVersion"], doc["kind"])
        assert klass is not None, f"{fichier}: modèle introuvable pour {doc['kind']}"
        _valider(doc, klass, f"{fichier}:{doc['kind']}", erreurs)
    assert not erreurs, "champs invalides :\n" + "\n".join(erreurs)


def test_tous_les_documents_sont_nommes_et_typés():
    for fichier, doc in _documents():
        assert doc.get("apiVersion"), fichier
        assert doc.get("kind"), fichier
        assert doc["metadata"].get("name"), f"{fichier}:{doc['kind']}"


# --- invariants du projet -------------------------------------------------

def test_un_seul_worker_et_jamais_deux_en_meme_temps():
    """Deux workers proposeraient chacun une génération sur le même état et
    consommeraient les mêmes intentions. RollingUpdate en ferait cohabiter
    deux le temps d'un roulement."""
    w = _nomme("Deployment", "worker")
    assert w["spec"]["replicas"] == 1
    assert w["spec"]["strategy"]["type"] == "Recreate"


def test_l_api_ne_monte_aucun_volume_d_etat():
    """L'API n'exécute rien : ni workspaces, ni candidats, ni dépôt git."""
    api = _nomme("Deployment", "api")
    montages = api["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    chemins = {m["mountPath"] for m in montages}
    assert not {"/app/workspaces", "/app/candidates"} & chemins


def test_les_deux_pods_visent_la_meme_archive():
    """C'est tout le point du passage à Postgres : sans mémoire partagée,
    l'API écrirait des verdicts que le worker ne lirait jamais."""
    dsn = set()
    for nom in ("api", "worker"):
        env = _nomme("Deployment", nom)["spec"]["template"]["spec"]["containers"][0]["env"]
        dsn |= {e["value"] for e in env if e["name"] == "CROSSPATCH_ARCHIVE"}
    assert len(dsn) == 1, dsn
    assert dsn.pop().startswith("postgresql://")


def test_aucun_secret_en_clair_dans_les_manifests():
    """Le Secret se crée en ligne de commande ; rien ne transite par git."""
    for f in K8S.glob("*.yaml"):
        if f.name == "secret.example.yaml":
            continue
        texte = f.read_text()
        assert "sk-or-" not in texte, f
        assert "e2b_" not in texte, f
        assert "kind: Secret" not in texte, f


def test_les_secrets_arrivent_par_reference():
    for nom in ("api", "worker"):
        c = _nomme("Deployment", nom)["spec"]["template"]["spec"]["containers"][0]
        refs = {e["secretRef"]["name"] for e in c["envFrom"]}
        assert refs == {"crosspatch-secrets"}, nom


def test_les_conteneurs_tournent_sans_privileges():
    for kind, nom in (("Deployment", "api"), ("Deployment", "worker")):
        pod = _nomme(kind, nom)["spec"]["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True, nom
        assert pod["securityContext"]["runAsUser"] == 10001, nom
        c = pod["containers"][0]
        assert c["securityContext"]["allowPrivilegeEscalation"] is False, nom
        assert c["securityContext"]["capabilities"]["drop"] == ["ALL"], nom


def test_l_uid_des_manifests_est_celui_de_l_image():
    """Un décalage ici et les volumes sont montés inaccessibles."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "--uid 10001" in dockerfile and "USER 10001" in dockerfile


def test_l_api_a_des_sondes_sur_un_endpoint_reel():
    c = _nomme("Deployment", "api")["spec"]["template"]["spec"]["containers"][0]
    for sonde in ("readinessProbe", "livenessProbe"):
        assert c[sonde]["httpGet"]["path"] == "/healthz", sonde
    assert "/healthz" in (ROOT / "api" / "main.py").read_text()


def test_le_worker_garde_ses_workspaces_et_ses_candidats():
    """Perdre les workspaces remet A et B à la graine ; perdre les candidats
    casse une promotion dont le verdict arrive après un redémarrage."""
    c = _nomme("Deployment", "worker")["spec"]["template"]["spec"]["containers"][0]
    persistants = {m["mountPath"] for m in c["volumeMounts"] if m["name"] == "state"}
    assert persistants == {"/app/workspaces", "/app/candidates"}


def test_la_config_vient_du_depot_pas_d_une_copie():
    k = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    fichiers = k["configMapGenerator"][0]["files"]
    assert fichiers == ["config.yaml=../../config.yaml"]
    for nom in ("api", "worker"):
        c = _nomme("Deployment", nom)["spec"]["template"]["spec"]["containers"][0]
        montages = {m["mountPath"] for m in c["volumeMounts"]}
        assert "/app/config.yaml" in montages, nom


def test_kustomization_liste_tous_les_manifests():
    k = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    sur_disque = {f.name for f in K8S.glob("*.yaml")} - {
        "kustomization.yaml", "secret.example.yaml"}
    assert set(k["resources"]) == sur_disque
