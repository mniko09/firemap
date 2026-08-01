# Déploiement FIREMAP — de GitHub à Render

Ce document explique comment la carte est passée de "fichiers sur mon PC" à
"URL publique que n'importe qui peut ouvrir" : https://firemap-khiu.onrender.com

## Vue d'ensemble

```
Ton PC (E:\Firemap)  --git push-->  GitHub  --lit le code-->  Render  --construit et lance-->  URL publique
   (le code source)                (stocke le code)         (heberge l'appli)
```

- **GitHub** ne fait que stocker le code (comme un Dropbox pour code, avec historique des versions). Il n'exécute rien.
- **Render** est l'hébergeur : il va chercher le code sur GitHub, le construit en une application qui tourne, et lui donne une adresse web publique.
- À chaque fois que tu pousses (`git push`) un changement sur GitHub, Render reconstruit et redéploie automatiquement (c'est le lien qu'on a vu se déclencher tout seul).

## C'est quoi Docker ?

Docker permet d'empaqueter une application avec **tout ce dont elle a besoin pour tourner** (Python, les librairies comme rasterio/geopandas, le code, les fichiers de données) dans un seul paquet standardisé, qui fonctionne à l'identique sur n'importe quelle machine — ton PC, le serveur de Render, ou ailleurs.

Analogie : un conteneur maritime. Peu importe ce qu'il y a dedans, sa taille standard fait qu'il se charge sur n'importe quel bateau/camion/grue. Docker fait la même chose pour du logiciel : peu importe la machine qui l'accueille, le conteneur contient tout ce qu'il faut pour fonctionner pareil partout.

Le vocabulaire :
- **Dockerfile** = la recette écrite (`E:\Firemap\Dockerfile`) : "pars d'un Linux avec Python 3.12, installe ces librairies, copie ces fichiers, lance cette commande au démarrage."
- **Image** = le paquet une fois construit à partir de la recette (comme un plat une fois cuisiné à partir de la recette).
- **Conteneur** = une instance de cette image en train de tourner (le plat servi et en train d'être mangé).

Render lit notre `Dockerfile`, construit l'image, puis démarre un conteneur à partir de cette image — c'est ce conteneur qui répond quand quelqu'un ouvre l'URL.

## Ce qui a été fait, étape par étape

1. **Code écrit en local** au fil des Phases 0 à 6 (dans `E:\Firemap`).
2. **Préparation du paquet Docker** :
   - `Dockerfile` : la recette de construction.
   - `.dockerignore` : liste des fichiers à NE PAS inclure dans le paquet (la venv Python, tes clés API dans `.env`, les données brutes volumineuses `data/raw/`) — pour que le paquet reste léger et qu'aucun secret ne parte sur GitHub/Render.
3. **Dépôt Git initialisé en local** (`git init`) : Git garde un historique des versions du code. Un premier "commit" (photo instantanée du projet) a été créé avec tout le code + les données déjà calculées (rasters, geojson) — **sans** les clés API, exclues via `.gitignore`.
4. **Repo GitHub créé par toi** (`github.com/mniko09/firemap`), puis le code poussé (`git push`) depuis ta machine vers GitHub.
5. **Compte Render créé par toi**, connecté à ce repo GitHub. Render a automatiquement détecté le `Dockerfile`.
6. **Premier build : échec.** L'image de base choisie (`python:3.12-slim`, une version "allégée" de Linux) ne contenait pas une librairie système (`libexpat`) dont `rasterio` (la librairie qui lit nos fichiers raster) a besoin pour fonctionner. Erreur visible dans les logs Render : `ImportError: libexpat.so.1: cannot open shared object file`.
7. **Correction** : changement de l'image de base pour `python:3.12` (version complète, plus de librairies système pré-installées, donc moins de risque de dépendance manquante). Nouveau commit, nouveau push.
8. **Render a reconstruit automatiquement** au push suivant, et cette fois le conteneur a démarré correctement → l'URL publique fonctionne.

## Pourquoi le chargement est parfois lent (~50 secondes)

Le plan **gratuit** de Render met le service **en veille après une période d'inactivité** (pour ne pas gaspiller des ressources gratuites sur des services que personne ne visite). Tu l'as vu toi-même dans les logs Render : *"Your free instance will spin down with inactivity, which can delay requests by 50 seconds or more."*

Concrètement : si personne n'a ouvert le lien depuis un moment, le conteneur est arrêté. Le premier visiteur qui rouvre le lien déclenche son redémarrage complet (30-60 secondes), avant que la page ne réponde. Une fois réveillé, il reste rapide tant qu'il y a du trafic régulier (quelques minutes d'inactivité max avant re-mise en veille sur le plan gratuit).

Un facteur secondaire, beaucoup plus petit (quelques centaines de ms à 1-2s) : la première fois qu'une couche (NDVI, pente, etc.) est demandée, le serveur la génère à la volée depuis le fichier raster puis la garde en cache pour les requêtes suivantes.

**Pour une vraie démo à Brault** : ouvre le lien 1-2 minutes avant le rendez-vous pour "réveiller" le service à l'avance. Pour éliminer complètement ce délai, il faudrait passer sur un plan payant Render (pas de mise en veille).

## Est-ce un "site pro" ?

**Oui et non, selon ce qu'on entend par là :**

Ce qui est déjà "pro" : URL publique HTTPS, accessible depuis n'importe quel navigateur/appareil, sans que ton PC ait besoin d'être allumé. Suffisant pour montrer le prototype à Brault ou faire une démo à un maire.

Ce qui ne l'est **pas** (limites du plan gratuit / du stade prototype) :
- Pas de nom de domaine personnalisé (`firemap-khiu.onrender.com`, pas `firemap.selvert.fr`)
- Mise en veille après inactivité (voir ci-dessus) — pas de garantie de disponibilité continue
- **Aucune authentification** : n'importe qui avec le lien peut y accéder — acceptable pour une démo interne, pas pour un vrai service public avec des données sensibles
- **Donnée figée** (instantané du 01/08/2026, pas de rafraîchissement automatique — cf. la Phase 6)
- Pas de sauvegarde, pas de supervision (monitoring/alertes), pas de plan de montée en charge si plusieurs communes l'utilisaient

**En résumé** : c'est un prototype de démonstration public et fonctionnel, pas un service en production. Pour vendre ça à des communes un jour, il faudrait : plan payant (ou autre hébergeur), nom de domaine, rafraîchissement automatique des données, et une vraie revue de sécurité — tout ça a du sens à en discuter avec Brault en Phase 7.
