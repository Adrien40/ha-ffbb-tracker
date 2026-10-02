# Journal des modifications

Toutes les modifications notables de ce projet sont consignées dans ce fichier.
Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## [0.8.7] - 2026-10-02

### Corrigé

- **Le code postal du gymnase n'était jamais récupéré.** L'API FFBB le fournit
  sur la commune du gymnase (`salle.commune.codePostal`), pas sur le gymnase
  lui-même, et la requête ne le demandait pas. Les adresses des gymnases, les
  liens de navigation (`geo:`, Google Maps, Waze) et le lieu des événements du
  calendrier incluent maintenant le code postal.
- **Les gros attributs des capteurs ne cassent plus l'historique.** Le
  `calendar` de toute la saison (capteur poule) et le tableau `standings`
  complet (capteur classement) peuvent dépasser la limite de 16 Ko du recorder
  de Home Assistant : *tous* les attributs du capteur étaient alors écartés de
  l'historique, avec un avertissement dans les journaux. Ces deux attributs sont
  désormais exclus du recorder. Ils restent pleinement disponibles en direct
  pour les tableaux de bord, les templates et les automatisations.
- **Les blueprints Telegram affichaient l'heure UTC.** L'heure du coup d'envoi
  et le jour du match étaient formatés directement depuis l'horodatage UTC :
  un match à 20h00 était annoncé à 19h00 (ou 18h00 en été), et le jour pouvait
  être faux autour de minuit. Le rappel de la veille et le bandeau « dans N
  jours » s'appuyaient en plus sur le fuseau horaire du système hôte plutôt que
  sur celui de Home Assistant. Tout suit maintenant le fuseau horaire configuré
  dans Home Assistant.
- **L'option « fenêtre après le match » fonctionne maintenant au-delà de 3
  heures.** Les options acceptent de 1 à 6 heures, mais un match non joué
  sortait toujours du prochain match après 3 heures codées en dur : les valeurs
  de 4 à 6 heures n'avaient aucun effet, et le polling rapide s'arrêtait trop
  tôt alors que le résultat était encore attendu. Le match reste désormais le
  prochain match pendant la plus longue des deux durées : 3 heures ou la
  fenêtre configurée. Les fenêtres de 1 à 3 heures se comportent exactement
  comme avant.
- **Les actions disparaissaient pendant un rechargement.** `refresh`,
  `get_next_matches` et `get_standings` étaient enregistrées au chargement
  d'une équipe et retirées au déchargement de la dernière. Comme chaque
  changement d'options recharge l'entrée, une automatisation appelant l'une
  d'elles à ce moment échouait avec « service introuvable », et les
  automatisations qui les référencent ne pouvaient pas être validées tant
  qu'aucune équipe n'était chargée. Elles sont maintenant enregistrées une
  seule fois au démarrage et ne sont jamais retirées.

### Modifié

- Cibler, avec un `entry_id` explicite, une équipe qui n'est pas chargée
  (configuration en cours ou en échec, entrée désactivée) lève maintenant une
  erreur claire `entry_not_loaded`, au lieu de ne rien faire ou de renvoyer un
  résultat vide. Les appels sans `entry_id` sont inchangés : les équipes non
  chargées sont ignorées.
- Décharger une équipe ne vide plus l'état partagé de l'intégration. Le rate
  limiter qui espace les requêtes est conservé entre deux rechargements, et les
  données en cache d'une poule ne sont supprimées que lorsqu'aucune équipe
  chargée ne suit plus cette poule.

### Notes de mise à jour

- **Mettez à jour les blueprints.** Home Assistant copie les blueprints lors de
  leur import et ne les met pas à jour avec l'intégration. Pour obtenir la
  correction du fuseau horaire, mettez à jour `match_notifications_telegram.yaml`
  et `match_result_notification_telegram.yaml`. Seul le blueprint de résultat
  déclare une URL source : c'est le seul à proposer *Réimporter le blueprint*
  sur la page des blueprints ; pour l'autre, copiez le fichier depuis
  `blueprints/automation/ffbb_tracker/` par-dessus votre copie existante (ou
  importez-le à nouveau depuis son URL GitHub).
- **Attendez-vous à une notification supplémentaire.** Le capteur de lieu
  inclut désormais le code postal : sa valeur change une fois après la mise à
  jour. Si vous utilisez le blueprint « Notifications de match », ce changement
  est signalé une seule fois comme « Mise à jour du match ». Il ne se répète
  pas.
- Les attributs `calendar` et `standings` ne sont plus conservés dans
  l'historique. Les données déjà enregistrées ne sont pas affectées.

### Interne

- Suite de tests portée de 256 à 276 tests, dont des tests de rendu qui
  chargent les vrais fichiers de blueprint et évaluent leurs templates dans le
  fuseau horaire Europe/Paris.
- Le fixture de test reproduit maintenant la vraie forme de l'API pour le code
  postal du gymnase, ce qui explique pourquoi le champ manquant n'avait jamais
  été détecté.
- `quality_scale.yaml` mis à jour pour refléter l'endroit où les actions sont
  enregistrées.
