# FFBB Tracker - Journal des modifications

## 0.8.7

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

Cette version corrige cinq bugs trouvés lors d'un audit (le code postal du gymnase, une limite de taille du recorder, les heures UTC des blueprints Telegram, la fenêtre de polling live et les actions du service) et reconstruit la CI : un workflow par contrôle, un seuil de couverture, et des releases GitHub publiées depuis ce journal.

### 🐛 Corrections
- **Le code postal du gymnase n'était jamais récupéré.** L'API FFBB le fournit sur la commune du gymnase (`salle.commune.codePostal`), pas sur le gymnase lui-même, et la requête ne le demandait pas. Les adresses des gymnases, les liens de navigation (`geo:`, Google Maps, Waze) et le lieu des événements du calendrier incluent maintenant le code postal.
- **Les gros attributs des capteurs ne cassent plus l'historique.** Le `calendar` de toute la saison (capteur poule) et le tableau `standings` complet (capteur classement) peuvent dépasser la limite de 16 Ko du recorder de Home Assistant : *tous* les attributs du capteur étaient alors écartés de l'historique, avec un avertissement dans les journaux. Ces deux attributs sont désormais exclus du recorder ; ils restent pleinement disponibles en direct pour les tableaux de bord, les templates et les automatisations.
- **Les blueprints Telegram affichaient l'heure UTC.** L'heure du coup d'envoi et le jour du match étaient formatés directement depuis l'horodatage UTC : un match à 20h00 était annoncé à 19h00 (ou 18h00 en été), et le jour pouvait être faux autour de minuit. Le rappel de la veille et le bandeau « dans N jours » s'appuyaient en plus sur le fuseau horaire du système hôte plutôt que sur celui de Home Assistant. Tout suit maintenant le fuseau horaire configuré dans Home Assistant.
- **L'option « fenêtre après le match » fonctionne maintenant au-delà de 3 heures.** Les options acceptent de 1 à 6 heures, mais un match non joué sortait toujours du prochain match après 3 heures codées en dur : les valeurs de 4 à 6 heures n'avaient aucun effet, et le polling rapide s'arrêtait trop tôt alors que le résultat était encore attendu. Le match reste désormais le prochain match pendant la plus longue des deux durées : 3 heures ou la fenêtre configurée. Les fenêtres de 1 à 3 heures se comportent exactement comme avant.
- **Les actions disparaissaient pendant un rechargement.** `refresh`, `get_next_matches` et `get_standings` étaient enregistrées au chargement d'une équipe et retirées au déchargement de la dernière. Comme chaque changement d'options recharge l'entrée, une automatisation appelant l'une d'elles à ce moment échouait avec « service introuvable », et les automatisations qui les référencent ne pouvaient pas être validées tant qu'aucune équipe n'était chargée. Elles sont maintenant enregistrées une seule fois au démarrage, dans `async_setup`, et ne sont jamais retirées.

### 🛡️ Renforcement
- Cibler, avec un `entry_id` explicite, une équipe qui n'est pas chargée (configuration en cours ou en échec, entrée désactivée) lève maintenant une erreur claire `entry_not_loaded`, au lieu de ne rien faire ou de renvoyer un résultat vide. Les appels sans `entry_id` sont inchangés : les équipes non chargées sont ignorées.
- Décharger une équipe ne vide plus l'état partagé de l'intégration : le rate limiter qui espace les requêtes est conservé entre deux rechargements, et les données en cache d'une poule ne sont supprimées que lorsqu'aucune équipe chargée ne suit plus cette poule.

### 🧰 Maintenance
- CI : un workflow par contrôle, donc un badge chacun : `tests.yaml` (pytest + couverture, 95 % minimum, le build échoue en dessous ; l'envoi vers Codecov est supprimé), `ruff.yaml`, `mypy.yaml`, `hassfest.yaml` et `hacs.yaml` (extraits de l'ancien `validate.yaml`), plus `release.yaml`. `codeql.yaml` est inchangé.
- Typage : la CI lance maintenant `mypy --strict` dans son propre workflow *Typing*, comme le prétendait déjà la règle Platinum `strict-typing` (il n'était lancé que sans `--strict`). Il passe sans aucune erreur.
- Releases : pousser un tag de version (`v0.8.7`) publie la release GitHub à partir de ce journal, avec la section française repliée. Le tag doit correspondre à la version du manifest et ce fichier doit contenir une section `## 0.8.7` correspondante, sinon la release échoue au lieu de publier des notes vides (`scripts/release_notes.py`).
- `pyproject.toml` limite la couverture à l'intégration (`pytest --cov` sans argument), et `mypy` est ajouté à `requirements_test.txt`.
- Suite de tests passée de 256 à 346 tests, 99 % de couverture : tests de rendu des blueprints (les vrais fichiers, évalués dans le fuseau Europe/Paris), tests du cycle de vie des services, tests du script de release, et tests de cohérence qui maintiennent les badges des README, les journaux, les workflows et `quality_scale.yaml` en accord avec le dépôt.
- Le fixture de test reproduit maintenant la vraie forme de l'API pour le code postal du gymnase, ce qui explique pourquoi le champ manquant n'avait jamais été détecté.
- `quality_scale.yaml` mis à jour pour refléter l'endroit où les actions sont enregistrées.

### 📚 Documentation
- Badges des README corrigés et complétés : les badges de workflows pointaient vers des fichiers `.yml` alors que les workflows sont en `.yaml`, donc aucun ne pouvait afficher de statut. Il y a maintenant des badges pour les tests, HACS, Hassfest, le lint, `mypy --strict` et CodeQL, dans les deux langues.
- Ajout de `CHANGELOG.md` et `CHANGELOG.fr.md`.

### 📋 Notes de mise à jour
- **Mettez à jour les blueprints.** Home Assistant copie les blueprints lors de leur import et ne les met pas à jour avec l'intégration. Pour obtenir la correction du fuseau horaire, mettez à jour `match_notifications_telegram.yaml` et `match_result_notification_telegram.yaml`. Seul le blueprint de résultat déclare une URL source : c'est le seul à proposer *Réimporter le blueprint* sur la page des blueprints ; pour l'autre, copiez le fichier depuis `blueprints/automation/ffbb_tracker/` par-dessus votre copie existante (ou importez-le à nouveau depuis son URL GitHub).
- **Attendez-vous à une notification supplémentaire.** Le capteur de lieu inclut désormais le code postal : sa valeur change une fois après la mise à jour. Si vous utilisez le blueprint « Notifications de match », ce changement est signalé une seule fois comme « Mise à jour du match ». Il ne se répète pas.
- Les automatisations qui passent l'`entry_id` d'une équipe désactivée ou qui n'a pas pu se charger reçoivent maintenant l'erreur `entry_not_loaded` au lieu d'un résultat vide.
- Les attributs `calendar` et `standings` ne sont plus conservés dans l'historique. Les données déjà enregistrées ne sont pas affectées.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀
