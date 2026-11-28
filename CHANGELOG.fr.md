# FFBB Tracker - Journal des modifications

Toutes les modifications notables du projet sont documentées dans ce fichier.
Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## [1.1.0] — 2026-11-28

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

### ✨ Nouveautés
- Exposition homogène de `formatted_address` sur `next_match_location`, `last_match_date` (avec `gym_address`), chaque ligne de l'attribut `calendar` du capteur `poule` et la réponse de l'action `ffbb_tracker.get_next_matches` (avec `postal_code`), afin que les cartes Lovelace et les automatisations disposent partout de l'adresse complète formatée.
- Exposition de `initial_points` (et de l'alias `irregularities` pour la colonne **`I`** de la carte Lovelace) à partir de `classements.pointsInitiaux`, ainsi que de `engagement_id` sur le capteur `poule`, chaque ligne de `standings` et chaque rencontre de `calendar` (`home_engagement_id` / `away_engagement_id`).
- Affichage de « `Exempt` » (au lieu de « `N/A` ») pour la ligne sans engagement (`idEngagement: null`) des poules impaires dans le classement, en cohérence avec les journées exemptes du calendrier.

### 🔄 Changements
- Simplification des flux de réparation `season_rollover` et `phase_change` : le choix de suivre l'équipe ouvre directement la proposition de candidat ou la recherche d'équipe, sans écran intermédiaire vide, tout en rappelant que les entités existantes sont conservées.
- Ajout de `{team_name}` dans le titre de l'alerte de réparation `token_refresh_failing` pour identifier immédiatement l'équipe concernée lorsqu'on en suit plusieurs.

### 🐛 Corrections
- **Logos du classement** : ajout de `classements.idEngagement.idOrganisme.logo` à `_POULE_FIELDS` afin que les logos des clubs soient disponibles dès la publication du classement en début de saison ou de nouvelle phase, même avant la saisie des rencontres.
- **Cadence de scrutation en cas d'erreur** : réinitialisation de `update_interval` à l'intervalle de base configuré dès que `_async_update_data` lève `UpdateFailed`, évitant que le coordinateur reste bloqué sur un réveil à 30 s ou un mode direct à 2 min pendant une panne réseau, un HTTP 429 ou un 404 de fin de saison.
- **Alerte de renouvellement du jeton public** : évaluation de `token_refresh_failures` même lorsque `_async_update_data` échoue avec `FFBBApiError` (notamment quand le renouvellement dynamique et le jeton de secours sont tous deux rejetés en HTTP 401/403).
- **Filet de sécurité anti-cache obsolète (`recheck`)** : rotation de la forme de requête `refresh` par pas de 10 minutes (au lieu d'une heure) pendant la fenêtre post-match précoce, exécution du recheck pour les équipes sœurs partageant un cache de poule (avec délai anti-rafale mutualisé par poule), prise en compte des matchs reportés à une date future dans la réponse fraîche, et exclusion des journées « `Exempt` » des résultats en attente.
- **Cycle de vie des réparations et migrations** : suppression systématique des trois alertes de réparation (`season_rollover`, `phase_change`, `token_refresh_failing`) et de l'horloge 404 persistée lors de la suppression, reconfiguration ou bascule d'une entrée ; fermeture immédiate des réparations orphelines si l'entrée a été supprimée ; purge de l'ancien cache de poule avant mise à jour de `poule_id` ; remise à zéro de `rank_evolution` et non-restauration des anciens événements `match_finished` / `rank_changed` lors d'un changement d'équipe ou d'un changement de poule à `engagement_id` constant.
- **Événement `match_finished`** : mémorisation de `_known_last_match_id` uniquement lorsque `last_match.result` est renseigné, afin qu'un match marqué joué avant la saisie des scores déclenche bien l'événement une fois les scores publiés.
- **Chargement des 404 au démarrage** : sérialisation de `_async_load_not_found` par un `asyncio.Lock` pour éviter toute condition de course lors du démarrage simultané de plusieurs coordinateurs.
- **Rencontres, fuseaux horaires, calendrier et série de forme** : interprétation en `Europe/Paris` des dates FFBB naïves sans fuseau quelle que soit la zone horaire de Home Assistant ; tri des matchs joués sans date (forfaits) avant les matchs joués datés afin qu'ils ne restent pas figés comme `last_match` toute la saison ; exclusion des exempts marqués joués de `last_match` ; alignement de l'ordre du score (`domicile - extérieur`) sur l'intitulé de l'événement calendrier avec vérification des deux scores ; calcul de `current_streak` (`FFBBFormSensor`) sur toute la saison ; prise en charge des codes langue régionaux BCP-47 (`fr-FR`, `fr-BE`, `fr-CA`) sur `form` et `calendar`.
- **Robustesse JSON, recherche d'équipe et limites d'état** : ancrage de `URL_ID_PATTERN` à un segment numérique complet après `/equipe(s)` ; remontée fidèle des erreurs du fallback de recherche de club (`too_many_clubs`, `cannot_connect`, `unknown`) ; absence de pré-sélection de la 1ʳᵉ équipe quand un filtre ne renvoie aucun résultat ; protection contre les dates ou champs d'adresse non-chaînes, les booléens dans `_safe_int`, les relations Directus scalaires aplaties et les états texte dépassant 255 caractères.
- **Blueprints Telegram et documentation** : descriptions explicites des sélecteurs du pack complet Telegram, filtrage strict sur `sensor.*` avec correspondances `_dernier_match_*` / `_last_match_*` et garde `result_grace_hours`, priorité aux capteurs permanents `last_match_*` au clic sur « Actualiser », passage des blueprints résultat et classement en `mode: queued` pour ne jamais annuler un callback manuel lorsqu'un événement filtré survient, et conversion en heure locale via `as_local(...)` dans les exemples Companion App des README.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## [1.0.0] — 2026-11-27

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

### ✨ Nouveautés
- Première version stable.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀
