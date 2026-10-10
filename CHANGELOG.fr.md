# FFBB Tracker - Journal des modifications

## 0.9.1

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

Cette version fait arriver beaucoup plus tôt le score final dans Home Assistant après un match.

### 🐛 Corrections
- **Un score pouvait arriver dans Home Assistant plus d'une heure après sa publication sur la FFBB.** Un score visible sur la FFBB à 15h07 n'arrivait qu'à 16h32. L'API FFBB peut continuer à répondre à la requête habituelle de l'intégration avec une copie antérieure au score, alors qu'une requête formulée autrement reçoit la bonne réponse. La sécurité ajoutée en 0.8.8 envoie cette seconde requête, mais seulement quand un match avait commencé plus de 3 heures plus tôt, et au plus une fois par heure : elle intervenait donc bien après la fin du match. Elle s'exécute maintenant dès 1 heure après le coup d'envoi, au plus toutes les 10 minutes pendant les 6 premières heures, puis toutes les heures comme avant (un match annulé ou forfait ne coûte donc pas une requête toutes les quelques minutes pendant une semaine). Elle ne concerne toujours qu'un match de votre équipe sans résultat et ne fait jamais échouer une mise à jour.

### 🧰 Maintenance
- Suite de tests passée de 488 à 490 tests : la sécurité est testée pour un match commencé il y a 30 minutes (aucune requête en plus), 90 minutes (revérifié tout de suite, puis toutes les 10 minutes) et 12 heures (toutes les heures).

### 📚 Documentation
- README : l'entrée de dépannage sur un score en retard décrit le nouveau calendrier.

### 📋 Notes de mise à jour
- Rien à faire. Pendant les heures qui suivent un match sans résultat, l'intégration peut envoyer jusqu'à 6 requêtes de plus par heure au lieu d'1. La liste des « résultats en retard » des diagnostics garde la règle des 3 heures.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## 0.9.0

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

Cette version fait en sorte que basculer une équipe sur ses nouveaux identifiants (reconfiguration, ou correction d'un changement de saison) conserve ses entités, et corrige le capteur d'évolution du classement, qui n'avait jamais gardé sa valeur d'un redémarrage à l'autre.

### 🐛 Corrections
- **Reconfigurer une équipe, ou corriger un changement de saison, recréait toutes ses entités.** Mesuré sur 18 entités : avec les mêmes noms d'équipe et de compétition, 17 identifiants d'entités survivaient (le 18e, renommé à la main, était réinitialisé) ; avec une équipe ou une compétition renommée, aucun. Un changement de saison renomme généralement l'un ou l'autre (catégorie d'âge, division, libellé de saison), donc les automatisations et tableaux de bord qui utilisaient ces entités cassaient, alors que le README affirmait le contraire. Les entités et l'appareil existants de l'équipe la suivent maintenant vers son nouvel engagement : mêmes identifiants d'entités, noms et identifiants personnalisés, historique et zone. Si quelque chose utilise déjà le nouvel identifiant, les entités sont recréées comme avant (un avertissement indique laquelle n'a pas pu être conservée).
- **Le capteur d'évolution du classement ne gardait jamais sa valeur d'un redémarrage à l'autre.** Il exposait les positions à sauvegarder sous un nom que Home Assistant ne lit pas : rien n'était jamais enregistré et l'évolution repartait de zéro à chaque redémarrage, alors que le README disait qu'elle était préservée. Corrigé : elle est maintenant conservée d'un redémarrage à l'autre (le premier redémarrage après la mise à jour repart encore de zéro, puisque rien n'avait jamais été enregistré). Elle repart aussi de zéro quand l'entrée est basculée sur une autre équipe, car des positions dans une autre poule ne sont pas comparables.

### 🧰 Maintenance
- Suite de tests passée de 473 à 488 tests, 99 % de couverture. La migration est testée par la vraie reconfiguration (quatre cas : mêmes noms, compétition renommée, équipe renommée, les deux) et par la réparation, avec une entité personnalisée à la main, une zone d'appareil et les cas délicats (une entité ou un appareil qui utilise déjà le nouvel identifiant). L'évolution du classement est testée par la sauvegarde et la restauration de Home Assistant lui-même, une couche que les tests précédents n'atteignaient pas : ils vérifiaient la propriété et la logique de restauration séparément, jamais que Home Assistant l'appellerait.
- Home Assistant laisse deux appareils partager les mêmes identifiants au lieu de refuser le second ; la migration vérifie donc elle-même une collision au lieu de compter sur une erreur.

### 📚 Documentation
- README : ce qu'il dit de la reconfiguration d'une équipe, et du capteur d'évolution après un redémarrage, décrit maintenant ce qui se passe réellement.

### 📋 Notes de mise à jour
- Rien à faire. Les entités qu'une reconfiguration précédente a déjà recréées gardent leurs identifiants actuels : seules les reconfigurations à partir de maintenant reportent les entités existantes.
- Au rechargement, l'appareil est renommé d'après la nouvelle équipe et la nouvelle compétition ; les identifiants d'entités ne changent pas avec lui.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## 0.8.9

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

Cette version rend les mises à jour plus robustes face aux à-coups de l'API FFBB, corrige la réparation de changement de saison (elle n'affichait rien à l'utilisateur), et distingue un nouveau match d'une modification dans les notifications Telegram.

### ✨ Nouveautés
- L'attribut `match_number`, déjà présent sur le capteur de date du prochain match, l'est maintenant aussi sur les capteurs adversaire et lieu du prochain match. Le blueprint de notifications s'en sert (voir plus bas).
- Le blueprint de notifications peut maintenant être mis à jour avec *Réimporter le blueprint*, comme celui de résultat : il déclare son URL source.

### 🐛 Corrections
- **La réparation de changement de saison ne faisait rien de visible.** Son bouton lançait la reconfiguration en arrière-plan, et Home Assistant n'affiche pas les flux lancés ainsi. La recherche d'équipe se fait maintenant dans la fenêtre de réparation : recherche par nom de club, URL ou identifiant d'équipe, choix de l'équipe, et l'entrée existante est basculée sur ses nouveaux identifiants (l'appareil de l'ancienne équipe est supprimé et l'entrée est rechargée). Elle réutilise la recherche du flux de configuration, donc les deux se comportent pareil. Deux défauts plus petits de cette fenêtre sont aussi corrigés : la première étape transmettait à l'étape de confirmation les données avec lesquelles Home Assistant démarre tout flux, qui les prenait pour une réponse et sautait l'explication, et les textes ne recevaient jamais le nom de l'équipe.
- **Le blueprint de notifications disait « Mise à jour du match » quand un nouveau match prenait la place du précédent** (par exemple juste après un résultat), au lieu de « Nouveau match programmé ». Il compare maintenant le numéro de match avant et après : un autre numéro est un nouveau match, le même numéro avec d'autres valeurs est une mise à jour.

### 🛡️ Renforcement
- **Les erreurs transitoires de l'API sont rejouées.** Les erreurs HTTP 429, 502, 503 et 504 sont rejouées jusqu'à deux fois, après le délai demandé par le serveur (`Retry-After`, en secondes ou sous forme de date) ou, sans indication, après 1 puis 2 secondes. Un serveur qui demande plus de 10 secondes fait échouer la mise à jour tout de suite au lieu de la retenir ; la prochaine interrogation prévue réessaie.
- **Chaque tentative HTTP a son propre délai de 15 s.** Le rafraîchissement du jeton et la nouvelle tentative partageaient le délai de la première requête.
- **Les messages d'erreur sont courts.** Une page d'erreur d'un proxy ou d'un CDN est réduite à du texte brut et coupée à 200 caractères, au lieu de remplir les journaux de HTML.
- **Les équipes d'une même poule n'attendent plus le même échec l'une après l'autre.** Quand une requête de poule échoue, les équipes qui attendaient derrière reçoivent cet échec immédiatement, au lieu de refaire chacune la requête et d'attendre le même délai. Une actualisation lancée ensuite interroge toujours l'API elle-même, donc le bouton *Actualiser* et la prochaine interrogation ne sont jamais bloqués.

### 🧰 Maintenance
- La compatibilité avec la plus ancienne version de Home Assistant prise en charge est maintenant vérifiée : la suite passe sur Home Assistant 2026.3.0 (le minimum de `hacs.json`) et 2026.3.1, ainsi que sur la dernière version, sans aucune modification de l'intégration. `tests.yaml` a un second job qui lance la suite sur 2026.3.1 (`requirements_test_min.txt`) ; l'outil de test n'a pas de version pour 2026.3.0 elle-même, c'est donc le premier correctif qu'il prend en charge.
- Code partagé : les cinq plateformes construisent leur appareil, et les deux capteurs de lieu leurs liens de navigation, en un seul endroit (`entity.py`), et la recherche d'équipe est partagée par le flux de configuration et la réparation (`team_picker.py`). Le comportement est inchangé. Une garde inatteignable du code d'interrogation est supprimée.
- Suite de tests passée de 401 à 473 tests, 99 % de couverture. La réparation de changement de saison est couverte de bout en bout par le gestionnaire de réparations de Home Assistant lui-même, la couche qui décide de ce que la fenêtre affiche et que les tests précédents n'exerçaient pas. De nouveaux tests de cohérence vérifient la configuration de test de la version minimale par rapport à `hacs.json` et au workflow.

### 📚 Documentation
- README : nouvelle entrée *Comment l'intégration s'identifie* dans les limites, qui explique la clé d'accès publique et les en-têtes de navigateur utilisés, et qu'elle n'utilise jamais d'identifiants. Les descriptions de la réparation de changement de saison correspondent maintenant à son fonctionnement.

### 📋 Notes de mise à jour
- **Mettez à jour le blueprint de notifications** (`match_notifications_telegram.yaml`) pour obtenir la formulation « Nouveau match programmé ». Home Assistant ne met pas les blueprints à jour avec l'intégration. Il déclare maintenant son URL source : après avoir remplacé votre copie une fois, *Réimporter le blueprint* fonctionne aussi pour lui.
- Dans le pire des cas, une mise à jour peut maintenant durer plus longtemps quand l'API peine (jusqu'à deux attentes de 10 secondes). Elle réussit plus souvent au lieu d'échouer et d'attendre la prochaine interrogation.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

## 0.8.8

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

Cette version ajoute une sécurité contre les réponses périmées de l'API FFBB, et un rapport de diagnostic pour comprendre pourquoi un score tarde.

### ✨ Nouveautés
- **Diagnostics de l'API.** Le téléchargement des diagnostics (menu ⋮ de l'appareil › *Télécharger les diagnostics*) contient maintenant une section `api` : quand l'API FFBB a été interrogée pour la dernière fois, les en-têtes de cache de ses réponses (`Age`, `Cache-Control`, `Date`, `ETag`, …), les matchs de votre équipe commencés il y a plus de 3 heures qui n'ont toujours pas de résultat (numéro du match, ancienneté, et les champs bruts `joue` / score), et ce que la sécurité ci-dessous a fait. Elle ne contient ni nom d'équipe, ni adresse, ni autre donnée personnelle.

### 🛡️ Renforcement
- **Sécurité contre les réponses périmées de l'API.** L'API FFBB a été observée, sur une poule, en train de répondre à la requête habituelle de l'intégration avec une copie des données antérieure à la publication des scores (et à un changement d'horaire) pendant plusieurs jours, alors qu'elle répondait correctement à une requête légèrement différente. Si un match de votre équipe a commencé il y a entre 3 heures et 7 jours et n'a toujours pas de résultat, l'intégration envoie maintenant une requête supplémentaire, formulée autrement (mêmes champs dans un autre ordre, `Cache-Control: no-cache`), et utilise sa réponse si elle contient plus de résultats. Elle s'exécute au plus une fois par heure, uniquement dans ce cas, et ne fait jamais échouer la mise à jour en cas d'erreur. Les requêtes normales sont inchangées.
- C'est une précaution : la cause des réponses périmées n'a pas pu être identifiée et n'était plus reproductible au moment de l'écriture, donc cette sécurité n'a pas été vue à l'œuvre sur une vraie réponse périmée. Les diagnostics montreront si elle se déclenche un jour.

### 🧰 Maintenance
- Suite de tests passée de 350 à 401 tests, 99 % de couverture. La requête normale de la poule est épinglée par un test (champs, paramètres et en-têtes exacts), puisque la sécurité repose sur le fait que sa requête supplémentaire soit différente.
- La règle « a un résultat » est maintenant une seule fonction, partagée par l'analyse des matchs et la sécurité (comportement inchangé).

### 📚 Documentation
- README : nouvelle entrée de dépannage pour un score en retard, qui explique comment télécharger les diagnostics.

🏀🏀🏀🏀🏀🏀🏀🏀🏀🏀

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
- Le manifest déclare maintenant `quality_scale: platinum`, en accord avec le badge des README. hassfest ne vérifie `quality_scale.yaml` que pour les intégrations du cœur : ce sont donc des tests qui le maintiennent cohérent. Il doit lister exactement les 54 règles de Home Assistant 2026.9.2, sans aucune règle ouverte et avec une raison pour chaque exemption.
- Suite de tests passée de 256 à 350 tests, 99 % de couverture : tests de rendu des blueprints (les vrais fichiers, évalués dans le fuseau Europe/Paris), tests du cycle de vie des services, tests du script de release, et tests de cohérence qui maintiennent les badges des README, les journaux, les workflows et `quality_scale.yaml` en accord avec le dépôt.
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
