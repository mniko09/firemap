# Déploiement — FIREMAP v2

Guide de mise en ligne. La **génération** d'une commune reste à la demande + cache
(cf. cahier §3.6) ; ce document couvre l'hébergement de l'API + du frontend.

---

## 1. Prérequis

| | |
|---|---|
| **Instance** | 2–4 vCPU / 4–8 Go RAM, ~40 Go disque pour démarrer (cf. `README` §5.2 du cahier). Fournisseur européen recommandé : Scaleway ou OVHcloud. |
| **Système** | Docker + Docker Compose v2 (`docker compose version`). |
| **Réseau** | ports 80/443 ouverts si TLS via Caddy ; sinon le port de l'API derrière le load-balancer du fournisseur. |
| **Domaine** | un nom de domaine (enregistrement `A`/`AAAA` vers l'instance) si TLS via Caddy. Le certificat est gratuit (Let's Encrypt). |
| **Identifiants** | Copernicus (`CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`) + Météo-France DPClim (`METEOFRANCE_API_KEY`). |

> ⚠️ Décisions qui reviennent à SELVERT avant la mise en ligne : le **fournisseur**, le **budget** (~30–60 €/mois, scénario économique), le **nom de domaine**, et la **liste des communes prioritaires** (`data/priority_communes.json`).

---

## 2. Installation

```bash
git clone -b v2 https://github.com/mniko09/firemap.git
cd firemap

# .env — À CRÉER, jamais commité (les 3 clés) :
cat > .env <<'ENV'
CDSE_CLIENT_ID=sh-xxxxxxxx
CDSE_CLIENT_SECRET=xxxxxxxx
METEOFRANCE_API_KEY=xxxxxxxx
# (si TLS via Caddy) FIREMAP_DOMAIN=firemap.selvert.fr
ENV

docker compose up -d --build
docker compose logs -f            # doit afficher "[scheduler] rafraichissement auto toutes les 12 h"
```

Vérification :

```bash
curl -s http://localhost:8000/api/health
# {"status":"ok","version":"0.1.0","communes_connues":0}
```

Ouvrir `http://<instance>:8000/` → la carte, par défaut Solliès-Pont (générée à la
première ouverture, ~2 min).

---

## 3. TLS / nom de domaine — deux options

**A. Caddy (autonome, HTTPS automatique)**

1. `FIREMAP_DOMAIN=...` dans le `.env`, le domaine pointe vers l'instance.
2. Dans `docker-compose.yml`, restreindre l'exposition directe : `ports: ["127.0.0.1:8000:8000"]`.
3. ```bash
   docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --build
   ```
   Caddy obtient et renouvelle le certificat tout seul (volume `caddy-data` à conserver).

**B. Load-balancer managé du fournisseur** (Scaleway / OVHcloud)

Le LB termine le TLS et transmet au port `8000` de l'instance. Ne pas lancer la
surcouche Caddy. Garder `ports: ["8000:8000"]` (ou l'IP privée) et restreindre au
LB via un groupe de sécurité.

---

## 4. Exploitation

### Surveiller

| Quoi | Comment |
|---|---|
| Générations en échec / en cours | `GET /api/communes` → champs `en_erreur`, `en_cours` ; détail par commune |
| Santé du service | `GET /api/health` (aussi le `healthcheck` Docker : `docker compose ps`) |
| Activité du pipeline / planificateur | `docker compose logs -f` → lignes `[<INSEE>] ...`, `[refresh] ...`, `[<INSEE>] ECHEC ...` |
| Quota Météo-France (50 req/min) | jamais atteint en usage normal ; la session HTTP retente automatiquement sur 429 |

### Rafraîchissement automatique

Le planificateur tourne **dans le conteneur**, toutes les 12 h (`--workers 1`
impératif, sinon plusieurs planificateurs). Passe manuelle :

```bash
curl -s http://localhost:8000/api/refresh/scan
```

### Communes prioritaires (« toujours prêtes »)

`data/priority_communes.json` vit dans le volume `firemap-data`. Modifier :

```bash
docker compose exec firemap sh -c 'cat data/priority_communes.json'
# éditer localement puis :
docker compose cp priority_communes.json firemap:/app/data/priority_communes.json
docker compose restart firemap        # ensure_priority_communes() (re)génère les manquantes
```

### Régénérer / forcer une commune

```bash
curl -X POST 'http://localhost:8000/api/communes/83130/generate?force=true'
```

---

## 5. Sauvegarde

Tout l'état est dans le volume **`firemap-data`** :

- `firemap.sqlite` — le registre (**à sauvegarder**, non régénérable).
- `priority_communes.json` — la liste prioritaire (**à sauvegarder**).
- `communes/<INSEE>/` — les rasters/tuiles (régénérables, mais lourds à refaire).

```bash
docker run --rm -v firemap-data:/d -v "$PWD":/b alpine \
  tar czf /b/firemap-data-$(date +%F).tgz -C /d .
```

Restauration : `tar xzf ... -C /d` dans un volume neuf avant le premier `up`.

---

## 6. Mise à jour du code

```bash
git pull
docker compose up -d --build           # + -f docker-compose.caddy.yml si Caddy
```

Le volume `firemap-data` est conservé. Au redémarrage, les générations restées
`running` sont repassées en `error` (reprise possible via `/generate`).

---

## 7. Dimensionnement (mesuré)

Test de charge local (`scripts/loadtest.py`, `uvicorn --workers 1`, ~1 cœur),
mélange tuiles / statut / valeur / page :

| Clients simultanés | Débit | p50 | p99 |
|---|---|---|---|
| 10 | ~75 req/s | 130 ms | 310 ms |
| 40 | ~55 req/s | 520 ms | 3,2 s |

- Un worker sature vers **10–15 clients actifs** ; le poste CPU dominant est le
  **rendu de tuile** (un COG lu par requête, limité par le GIL).
- `--workers 1` étant imposé par le planificateur in-process, le levier de montée
  en charge est un **cache/CDN devant les tuiles** : elles portent déjà
  `Cache-Control: immutable` et une URL versionnée (`?v=<genere_le>`).
- Pour l'usage SELVERT (interne + démos, quelques utilisateurs) : **2 vCPU / 1
  worker suffisent**. Réévaluer après quelques semaines d'usage réel.

---

## 8. Reste à faire (au-delà du 15/09, cf. cahier §7.2)

- Stockage objet S3 (Scaleway/OVH) pour `communes/` au lieu du disque local
  (le code écrit aujourd'hui sous `data/communes/`).
- Monitoring avancé + alerting (jobs en échec → notification), tableau de bord.
- Étude de montée en charge si l'usage dépasse les hypothèses.
- Séparer le planificateur du serveur web pour permettre `--workers N`.
