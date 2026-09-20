[![English](https://img.shields.io/badge/Language-English-red)](README.md) [![Français](https://img.shields.io/badge/Langue-Fran%C3%A7ais-blue)](#)

# FFBB Tracker pour Home Assistant 🏀
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/Adrien40/ha-ffbb-tracker)](https://github.com/Adrien40/ha-ffbb-tracker/releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://github.com/Adrien40/ha-ffbb-tracker/blob/main/LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-ffbb-tracker/tests.yml?branch=main&label=tests)](https://github.com/Adrien40/ha-ffbb-tracker/actions/workflows/tests.yml)
[![Validate](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-ffbb-tracker/validate.yml?branch=main&label=hassfest%2Fhacs)](https://github.com/Adrien40/ha-ffbb-tracker/actions/workflows/validate.yml)
[![Linting](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-ffbb-tracker/lint.yml?branch=main&label=lint)](https://github.com/Adrien40/ha-ffbb-tracker/actions/workflows/lint.yml)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-ffbb-tracker/codeql.yml?branch=main&label=codeql)](https://github.com/Adrien40/ha-ffbb-tracker/actions/workflows/codeql.yml)
[![Quality Scale](https://img.shields.io/badge/HA%20Quality%20Scale-Platinum-9c27b0)](custom_components/ffbb_tracker/quality_scale.yaml)

Une **intégration complète pour Home Assistant** qui suit les résultats, calendriers et classements de vos équipes de basketball engagées en championnats FFBB (Fédération Française de BasketBall), sans compte ni clé privée requise. 🛡️

> ℹ️ **À savoir** : cette intégration interroge l'API publique Directus utilisée par l'application web officielle `competitions.ffbb.com`, avec le même jeton d'accès public (lecture seule) et le même User-Agent de navigateur que le site officiel. Aucun compte ni identifiant personnel n'est nécessaire. Elle récupère l'ensemble des rencontres, résultats et classements de la poule en une seule requête optimisée.

> 🖥️ **Carte Lovelace disponible** : envie d'une carte de tableau de bord prête à l'emploi plutôt que de construire la vôtre ? Découvrez la carte [FFBB Tracker Card](https://github.com/Adrien40/ha-ffbb-tracker-card) — rencontres, scores en direct et classements dans une seule carte.


Si ce projet vous est utile, vous pouvez soutenir son développement 🙏

<a href="https://www.buymeacoffee.com/adrien40"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="160"></a>

---

## ⚡ En résumé
- 🏀 Suivi complet de vos équipes préférées (Départemental, Régional, National)
- 📅 Calendrier complet des matchs synchronisé nativement dans Home Assistant
- ⏱️ Prochain match en direct : date, adversaire, statut domicile ou extérieur
- 🏀 Capteurs binaires « Jour de match » et « Match en cours » prêts à l'emploi pour des automatisations simples, sans template
- 📍 Itinéraires en un clic : liens directs Google Maps et Waze vers la salle générés automatiquement dans les attributs
- 🏆 Dernier match joué : score, adversaire et issue de la rencontre (victoire, défaite, nul)
- 📊 Classement de la poule et suivi dynamique de l'évolution de la position (gain/perte de places) avec persistance après redémarrage
- 📈 Capteur de forme récente résumant les 5 derniers résultats (ex. `V-V-D-V-N`) avec la série en cours
- ⚡ Entités événementielles « Fin de match » et « Changement de classement » pour déclencher vos automatisations à l'instant précis, pas à chaque cycle de scrutation
- 🧩 Blueprints d'automatisation prêts à importer pour les notifications Telegram, sans template à écrire
- 🛠️ Trois actions de service prêtes pour vos automatisations et scripts de notifications (Telegram, alertes d'avant-match)
- 🔧 Notification de réparation automatique si une équipe devient introuvable (changement de saison), avec un lien direct vers Reconfigurer
- 🔍 Ajout en quelques secondes : recherche par nom de club, code officiel (ex. NAQ0040141) ou simple copier-coller de l'URL de l'équipe
- ⚙️ Installation via HACS en 2 minutes

---

## 📸 Aperçu dans Home Assistant

### 📊 Visualisation

<p align="center">
  <img src="https://raw.githubusercontent.com/Adrien40/ha-ffbb-tracker-card/main/docs/screenshots/dashboard_overview.png" width="500">
</p>

<p align="center">
  <em>📊 Vue d’ensemble des données dans Home Assistant</em>
</p>

### 🔍 Aperçu des entités
<p align="center">
  <img src="https://raw.githubusercontent.com/Adrien40/ha-ffbb-tracker/main/docs/screenshots/entities_overview.png" width="600" alt="Aperçu des entités">
</p>
<p align="center">
  <em>🔍 Entités créées automatiquement pour chaque équipe suivie</em>
</p>

### 📅 Calendrier officiel
<p align="center">
  <img src="docs/screenshots/calendar.png" width="500" alt="Détails de la rencontre dans le calendrier">
</p>
<p align="center">
  <em>📅 Calendrier des rencontres, adresse de la salle et résultats directement dans votre calendrier</em>
</p>

---

### 💡 Pourquoi cette intégration ?
Conçue pour les joueuses, joueurs, parents et supporters de basketball amateur ou professionnel souhaitant intégrer leur passion dans leur domotique :

* **🛡️ Zéro compte à créer :** aucun identifiant ni mot de passe personnel requis, l'intégration utilise le jeton de consultation public de l'application FFBB.
* **🚗 Guidage immédiat vers les salles :** plus besoin de chercher l'adresse du gymnase le samedi après-midi ; l'adresse complète avec code postal et les liens de navigation GPS sont prêts sur votre tableau de bord ou dans vos alertes mobiles.
* **📈 Suivi de classement fiable :** l'évolution de votre équipe au classement (+1, -2, =) est mémorisée dans le stockage interne de Home Assistant et survit aux redémarrages sans perte d'historique.
* **⚡ Économe et respectueuse :** une interrogation cadencée et centralisée par poule pour éviter de solliciter inutilement les serveurs FFBB.

---

### ✅ Compatibilité et prérequis
* 🏷️ **Compétitions supportées** : toutes les équipes et compétitions répertoriées sur `competitions.ffbb.com` (seniors, jeunes, championnats départementaux, régionaux et nationaux).
* ⚙️ **Version Home Assistant requise** : version 2026.3.0 ou supérieure.
* 🌐 **Connexion internet** : requise pour actualiser les données depuis les serveurs de la FFBB.
* 🔍 **Recherche simple** :
  * Par nom de club ou de commune (ex. : *Basket Landes*, *Paris*).
  * Par code officiel de club (ex. : *NAQ0040141*).
  * Par URL complète de la page équipe copiée depuis `competitions.ffbb.com` ou par identifiant numérique direct d'engagement.

---

### ✨ Points forts
* 📅 **Calendrier natif Home Assistant** : visualisez l'ensemble de la saison dans la vue Calendrier avec l'heure exacte, le numéro de journée, le score final et le nom complet de la salle.
* 🚗 **Navigation GPS intégrée** : liens profonds Waze et Google Maps prêts à l'emploi dans les attributs pour démarrer le guidage en un clic.
* 📈 **Capteur d'évolution de classement** : calcule le gain ou la perte de position entre deux mises à jour, avec icône dynamique adaptative (flèche montante, flèche descendante ou tiret).
* 📊 **Capteur de forme récente** : chaîne compacte des 5 derniers résultats (ex. `V-V-D-V-N`), avec le nombre de victoires/défaites/nuls et la série en cours en attributs.
* ⚡ **Entités événementielles pour des automatisations instantanées** : `event.*_match_finished` se déclenche une seule fois par nouveau résultat (victoire/défaite/nul), et `event.*_rank_changed` une seule fois quand la position en poule bouge réellement — les deux survivent aux redémarrages sans se redéclencher sur une ancienne donnée, contrairement à un déclencheur basé sur l'état d'un capteur.
* 🔄 **Reconfiguration simplifiée** : changez d'équipe ou de poule directement depuis le bouton « Reconfigurer » sans supprimer l'intégration ni laisser d'appareils fantômes dans le registre.
* 🛠️ **Actions dédiées (services)** :
  * `ffbb_tracker.refresh` : force l'actualisation manuelle immédiate des données.
  * `ffbb_tracker.get_next_matches` : renvoie les prochains matchs sous forme de dictionnaire exploitable par vos automatisations.
  * `ffbb_tracker.get_standings` : renvoie la grille complète du classement de la poule (points, victoires, défaites, matchs joués).
* 🔄 **Bouton d'actualisation manuelle** : une entité `button` pour forcer la mise à jour des données à tout moment sans attendre le cycle de scrutation.
* ⚙️ **Cadence dynamique et suivi de direct** : réglez l'intervalle de base (15 à 1 440 min), activez l'accélération les jours de match (2 à 15 min) et ajustez la fenêtre d'attente du score.

---

### 🚀 Installation

#### Via HACS (recommandé)
Ce dépôt n'étant pas encore dans la liste officielle par défaut, vous pouvez l'ajouter facilement en tant que dépôt personnalisé.

1. Ouvrez **HACS** dans votre interface Home Assistant.
2. Cliquez sur les 3 petits points en haut à droite et sélectionnez **Dépôts personnalisés**.
3. Dans le champ **Dépôt**, collez l'adresse : `https://github.com/Adrien40/ha-ffbb-tracker`
4. Dans le menu déroulant **Type**, choisissez **Intégration** puis cliquez sur **Ajouter**.
5. Une fois ajouté, cliquez sur **Télécharger** sur la fiche de l'intégration qui apparaît (sélectionnez la dernière version).
6. **Redémarrez complètement Home Assistant**.
7. Rendez-vous dans **Paramètres** > **Appareils et services** > **Ajouter une intégration**, puis cherchez **FFBB Tracker**.

#### Installation manuelle
1. Téléchargez la dernière archive depuis la page des [Releases](https://github.com/Adrien40/ha-ffbb-tracker/releases).
2. Copiez le dossier `custom_components/ffbb_tracker` dans le dossier `custom_components` situé à la racine de votre configuration Home Assistant.
3. Redémarrez Home Assistant.

---

### 📊 Capteurs et entités disponibles

Chaque équipe configurée crée un appareil dédié regroupant 12 capteurs, 2 capteurs binaires, 2 entités événementielles, 1 bouton et 1 calendrier :

| Entité | Classe / Unité | Description |
| :--- | :--- | :--- |
| 🔄 **Actualiser** | Bouton | Déclenche immédiatement une mise à jour manuelle des données de l'équipe. |
| 🗓️ **Calendrier des matchs** | Calendrier | Calendrier officiel regroupant tous les matchs prévus et terminés de la poule. |
| 🏀 **Jour de match** | Capteur binaire | Actif dès que l'équipe suivie a un match prévu aujourd'hui (date locale). |
| 🏟️ **Match en cours** | Capteur binaire | Actif peu avant le coup d'envoi jusqu'à la publication du résultat (ou l'expiration du délai d'attente). |
| ⚡ **Fin de match** | Événement | Se déclenche une fois qu'un nouveau résultat est publié, avec adversaire, score, écart de points, terrain (domicile/extérieur), journée et salle en données d'événement. |
| ⚡ **Changement de classement** | Événement | Se déclenche une fois que la position en poule bouge réellement, avec l'ancienne et la nouvelle position en données d'événement (`event.<equipe>_rank_changed`). |
| 📊 **Classement** | Entier | Position actuelle de l'équipe dans sa poule (`sensor.<equipe>_rank`). *(Contient le tableau complet du classement en attribut)* |
| 📈 **Classement : Évolution** | Texte | Évolution de la position (`+1`, `-2`, `0`) avec icône adaptative (`sensor.<equipe>_rank_evolution`). |
| 📉 **Forme récente** | Texte | Les 5 derniers résultats sous forme compacte (ex. `V-V-D-V-N`). *(Contient le nombre de victoires/défaites/nuls et la série en cours en attributs)* |
| 📅 **Dernier match : Date** | Timestamp | Date et heure du dernier match joué. |
| 👥 **Dernier match : Adversaire** | Texte | Nom du dernier adversaire affronté. |
| 🏆 **Dernier match : Résultat** | Statut | Résultat du dernier match : **Victoire** (`win`), **Défaite** (`loss`) ou **Nul** (`draw`). |
| 🔢 **Dernier match : Score** | Texte | Score final formaté (ex. : `78 - 65`). |
| 🏆 **Poule** | Texte | Intitulé officiel de la poule et de la phase en cours. |
| 📅 **Prochain match : Date** | Timestamp | Date et heure de début de la prochaine rencontre programmée. |
| 👥 **Prochain match : Adversaire** | Texte | Nom de l'adversaire du prochain match. *(Contient la salle et les URL Maps / Waze en attributs)* |
| 📍 **Prochain match : Lieu** | Texte | Adresse complète formatée de la salle où se dispute la rencontre. |
| 🏟️ **Prochain match : Terrain** | Statut | Indique si la rencontre a lieu à **Domicile** (`home`) ou à l'**Extérieur** (`away`). |

---

### 🗺️ Attributs de navigation GPS

Les entités `next_match_location` (Lieu) et `next_match_opponent` (Adversaire) exposent des attributs riches pour préparer vos déplacements :

* `gym_name` : nom du gymnase ou du complexe sportif
* `gym_address` : rue ou lieu-dit
* `gym_city` : commune
* `google_maps_url` : lien direct de guidage vers la salle pour Google Maps
* `waze_url` : lien de lancement immédiat de navigation pour l'application Waze

---

### 🖼️ Logos des clubs

Les entités `next_match_opponent` (Adversaire), `next_match_venue_type` (Terrain), `next_match_location` (Lieu), `last_match_date` (Date) et `last_match_opponent` (Adversaire) exposent :

* `team_logo_url` : le logo de votre club suivi, tel qu'enregistré auprès de la FFBB
* `opponent_logo_url` : le logo du club adverse

Les deux valent `None` lorsqu'un club n'a pas de logo enregistré à la FFBB (fréquent pour les petits clubs) — vérifiez toujours la présence d'une valeur avant de l'utiliser dans un template. Les deux entités « Adversaire » exposent en plus `entity_picture` avec le logo adverse, qui s'affiche alors nativement dans le journal, l'historique et la plupart des cartes d'entités, sans configuration supplémentaire.

---

### 🚀 Configuration

1. Rendez-vous dans **Paramètres** > **Appareils et services**.
2. Cliquez sur **Ajouter une intégration** et recherchez **FFBB Tracker**.
3. Renseignez votre recherche dans le formulaire :
   * **Recherche par club** : tapez quelques lettres du nom du club (ex. : `Bordeaux` ou `Basket Landes`). Sélectionnez ensuite le club souhaité, puis l'équipe engagée dans la liste proposée.
   * **Recherche directe** : collez directement l'adresse web de l'équipe copiée depuis le site de la FFBB (ex. : `https://competitions.ffbb.com/equipes/123456789`) ou collez simplement l'identifiant numérique de l'engagement.
4. La détection de la poule et de la compétition est automatique.

---

### 🛠️ Actions et automatisations (exemples)

#### Exemple : notification interactive veille de match et alertes (Application Companion)

<details>
<summary>📱 Voir l'exemple d'automatisation complète pour l'application mobile</summary>

```yaml
alias: Basket - Suivi Mon Équipe (Companion)
description: >-
  Rappel veille, alertes calendrier et actualisation interactive via l'application
  mobile
triggers:
  - trigger: time
    at: "19:00:00"
    id: rappel_veille
  - trigger: state
    entity_id:
      - sensor.mon_equipe_prochain_match_date
      - sensor.mon_equipe_prochain_match_lieu
      - sensor.mon_equipe_prochain_match_adversaire
    id: alerte_calendrier
  - trigger: event
    event_type: mobile_app_notification_action
    event_data:
      action: refresh_match
    id: callback_refresh
conditions: []
actions:
  - if:
      - condition: trigger
        id: callback_refresh
    then:
      - action: button.press
        target:
          entity_id: button.mon_equipe_actualiser
      - delay: "00:00:02"
  - if:
      - condition: trigger
        id: alerte_calendrier
    then:
      - delay: "00:00:02"
  - condition: template
    value_template: |-
      {% if trigger is not defined or trigger.id is not defined %}
        true
      {% elif trigger.id == 'callback_refresh' %}
        true
      {% elif trigger.id == 'rappel_veille' %}
        {% set match_dt = as_datetime(states('sensor.mon_equipe_prochain_match_date')) %}
        {{ match_dt is not none and match_dt.astimezone().date() == (now().date() + timedelta(days=1)) }}
      {% elif trigger.id == 'alerte_calendrier' %}
        {{ trigger.from_state is not none and
           trigger.to_state is not none and
           trigger.to_state.state not in ['unknown', 'unavailable', ''] and
           trigger.from_state.state != trigger.to_state.state }}
      {% else %}
        false
      {% endif %}
  - action: notify.mobile_app_smartphone
    data:
      title: 🏀 Mon Équipe
      message: >-
        {%- set dt =
        as_datetime(states('sensor.mon_equipe_prochain_match_date')) -%}

        {%- set days = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi',
        'Samedi', 'Dimanche'] -%}

        {%- set months = ['Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
        'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre'] -%}

        {%- set lieu = states('sensor.mon_equipe_prochain_match_lieu') -%}

        {%- set is_home = state_attr('sensor.mon_equipe_prochain_match_lieu',
        'is_home') -%}

        {%- if trigger is defined and trigger.id is defined and trigger.id ==
        'alerte_calendrier' -%}
          {%- if trigger.from_state is not none and trigger.from_state.state not in ['unknown', 'unavailable', ''] -%}
            {%- set statut = '⚠️ Mise à jour du match' -%}
          {%- else -%}
            {%- set statut = '📢 Nouveau match programmé' -%}
          {%- endif -%}
        {%- else -%}
          {%- if dt is not none -%}
            {%- set delta = (dt.astimezone().date() - now().date()).days -%}
            {%- set statut = '🔥 Match aujourd\'hui !' if delta == 0 else ('🔥 Match demain !' if delta == 1 else ('⏳ Match dans ' ~ delta ~ ' jours !' if delta > 1 else '🏀 Prochain match')) -%}
          {%- else -%}
            {%- set statut = '🏀 Prochain match' -%}
          {%- endif -%}
        {%- endif -%}

        {{ statut }}

        👥 Adversaire : {{ states('sensor.mon_equipe_prochain_match_adversaire')
        | title }}

        📅 Date : {% if dt is not none %}{{ days[dt.weekday()] }} {{ dt.day }}
        {{ months[dt.month - 1] }}{% else %}Date inconnue{% endif %}

        ⏰ Coup d'envoi : {% if dt is not none %}{{ dt.strftime('%Hh%M') }}{%
        else %}Inconnu{% endif %}

        🏟️ Terrain : {% if is_home %}🏠 Domicile{% else %}🚗 Extérieur{% endif
        %}

        📍 Lieu : {{ lieu | title if
        has_value('sensor.mon_equipe_prochain_match_lieu') else 'Non renseigné'
        }}
      data:
        tag: match_basket_notif
        group: match_basket
        notification_icon: mdi:basketball
        channel: Basket
        importance: high
        persistent: true
        sticky: true
        clickAction: noAction
        actions: >-
          {% set gmaps = state_attr('sensor.mon_equipe_prochain_match_lieu',
          'google_maps_url') | default('', true) %} {% set waze_clean =
          state_attr('sensor.mon_equipe_prochain_match_lieu', 'waze_url') |
          default('', true) | replace('+', '%20') %} {% set buttons =
          [{'action': 'refresh_match', 'title': '🔄
          Actualiser'}] %} {% if gmaps.startswith('http') %}
            {% set buttons = buttons + [{'action': 'URI', 'title': '🗺️ Maps', 'uri': gmaps}] %}
          {% endif %} {% if waze_clean.startswith('http') %}
            {% set buttons = buttons + [{'action': 'URI', 'title': '🚗 Waze', 'uri': waze_clean}] %}
          {% endif %} {{ buttons }}
mode: restart
max_exceeded: silent
```

</details>

#### Blueprints prêts à l'emploi (sans template)

Vous préférez cliquer plutôt qu'écrire du YAML ? Trois blueprints d'automatisation sont fournis dans [`blueprints/automation/ffbb_tracker`](blueprints/automation/ffbb_tracker), basés sur les entités `event` ci-dessus : ils ne se déclenchent qu'une fois par résultat/changement, jamais au redémarrage.

* **Pack complet de notifications Telegram** : rappel de veille de match et boutons interactifs (Actualiser, Maps, Waze) pour une équipe.
  [![Ouvrez votre instance Home Assistant et importez ce blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAdrien40%2Fha-ffbb-tracker%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fffbb_tracker%2Fmatch_notifications_telegram.yaml)
* **Notification de résultat de match** : message Telegram avec adversaire, score et terrain dès qu'un nouveau résultat tombe.
  [![Ouvrez votre instance Home Assistant et importez ce blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAdrien40%2Fha-ffbb-tracker%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fffbb_tracker%2Fmatch_result_notification_telegram.yaml)
* **Notification de changement de classement** : message Telegram avec l'ancienne et la nouvelle position dès qu'elle bouge.
  [![Ouvrez votre instance Home Assistant et importez ce blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAdrien40%2Fha-ffbb-tracker%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fffbb_tracker%2Frank_changed_notification_telegram.yaml)
  
---

### ⚠️ Limitations connues

* **Changement d'identifiant d'engagement entre les saisons** : la FFBB réattribue un nouvel identifiant d'engagement interne à chaque équipe à chaque saison. Si les entités d'une équipe suivie cessent de se mettre à jour et restent indisponibles plusieurs jours alors que la saison est manifestement en cours, la cause la plus probable est que l'identifiant d'engagement de l'équipe a changé. Passé 3 jours dans cet état, l'intégration lève désormais automatiquement une notification de réparation (**Paramètres** > **Système** > **Réparations**) qui pointe directement vers la solution. Dans tous les cas, utilisez **Paramètres** > **Appareils et services** > **FFBB Tracker** > **Reconfigurer** pour rechercher à nouveau l'équipe et la récupérer sous son nouvel identifiant — cela conserve vos automatisations et cartes de tableau de bord existantes, car l'appareil et les identifiants d'entités ne sont pas affectés par cette opération.
* **Pas d'API officielle** : cette intégration s'appuie sur les points d'accès Directus publics utilisés par l'application web officielle, et non sur une API documentée et stable. Des changements côté FFBB (modification du schéma, filtrage anti-robot plus strict) peuvent affecter l'intégration sans préavis ; consultez la section [Dépannage](#-dépannage) et ouvrez une [issue](https://github.com/Adrien40/ha-ffbb-tracker/issues) si quelque chose cesse de fonctionner.

---

### 🗑️ Désinstallation

1. Rendez-vous dans **Paramètres** > **Appareils et services**, ouvrez l'intégration **FFBB Tracker**, et supprimez chaque équipe configurée (menu à trois points > **Supprimer**). Cela retire également l'appareil associé et toutes ses entités du registre.
2. Si installé via HACS : ouvrez **HACS** > **FFBB Tracker**, puis sélectionnez **Supprimer**.
3. Si installé manuellement : supprimez le dossier `custom_components/ffbb_tracker` de votre répertoire de configuration Home Assistant.
4. Redémarrez Home Assistant.

Cette intégration ne crée aucun identifiant, jeton ou compte externe : il n'y a donc rien à révoquer ailleurs.

---

### 🐛 Dépannage

<details>
<summary>⚠️ Consulter les questions fréquentes</summary>

* **Aucune équipe trouvée lors de la recherche par club** : certaines associations sportives n'ont pas encore engagé leurs équipes pour la phase suivante, ou la poule n'est pas encore publiée par le comité ou la ligue.
* **Le lien GPS m'amène au centre-ville et non au gymnase** : sur certaines petites salles, la commune n'a pas renseigné d'adresse précise auprès de la FFBB. Le lien utilise alors le nom de la salle et la ville pour optimiser le calcul d'itinéraire.
* **Le capteur d'évolution affiche 0 après un redémarrage** : c'est normal lors de la toute première installation ; dès la prochaine actualisation du classement par la FFBB, le différentiel réel (+1, -1, etc.) sera automatiquement calculé et préservé.

</details>

---

### 🌐 Langues supportées

L'intégration est entièrement disponible en **Français** <img src="https://hatscripts.github.io/circle-flags/flags/fr.svg" width="16" valign="middle"> et en **Anglais** <img src="https://hatscripts.github.io/circle-flags/flags/gb.svg" width="16" valign="middle"> (interface de configuration, entités et calendrier).

Si vous souhaitez voir l'intégration traduite dans une autre langue ou contribuer à une traduction, vous pouvez ouvrir une [issue](https://github.com/Adrien40/ha-ffbb-tracker/issues) ou me contacter directement sur GitHub.

---

### 🤝 Contributions et support
Pour tout bug ou demande d'amélioration, merci d'ouvrir une [Issue](https://github.com/Adrien40/ha-ffbb-tracker/issues) sur ce dépôt.

### ⚖️ Licence et avertissement
Projet sous licence **GPLv3**. Il s'agit d'un projet indépendant, sans aucun lien officiel avec la Fédération Française de BasketBall (FFBB). L'utilisation de ce logiciel se fait sous votre propre responsabilité.

---

**Développé avec ❤️ par @Adrien40**

<a href="https://www.buymeacoffee.com/adrien40"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="180"></a>

<!-- Keywords: Home Assistant custom integration, FFBB, Basketball, basket, scores, standings, calendar, sports tracker, local automation -->
