# Référence de la simulation

Ce document décrit le modèle de simulation : ses concepts, ses composants, et la signification de chaque réglage de configuration. Il s'adresse aux utilisateurs qui ont besoin de comprendre le comportement de la simulation, pas son code source.

La lecture de ce document est un prérequis au [guide du Flow Designer](flow-designer.fr.md), qui emploie les concepts définis ici sans les redéfinir. L'interprétation des résultats d'exécution est traitée séparément dans la [référence des KPI](kpis.fr.md).

Le modèle a été développé à l'origine pour un atelier d'injection de cire et de fonderie à la cire perdue, et certains exemples reflètent ce contexte. Le modèle lui-même est indépendant du domaine : tout process dans lequel des articles traversent des stations et subissent des opérations peut être représenté.

---

## 1. Vue d'ensemble

La simulation représente une ligne de production. Les pièces sont créées par un générateur, traversent un réseau de buffers et de postes où elles sont traitées, et terminent soit dans un buffer de sortie (comptées comme produites), soit dans un buffer de rebut (éliminées).

```mermaid
flowchart LR
    G([Générateur de pièces]) --> B1[Buffer]
    B1 --> T1[Poste]
    T1 --> B2[Buffer]
    B2 --> T2[Poste]
    T2 --> R{Routeur}
    R -->|conforme| B3[Buffer]
    R -->|non conforme| S[Rebut]
    B3 --> EX[Sortie]
```

Deux principes s'appliquent partout :

- **Simulation à événements discrets.** Le temps interne se mesure en minutes simulées. Le moteur avance d'événement en événement (arrivée d'une pièce, fin d'une opération) plutôt que par incréments fixes. Des horizons de plusieurs années s'exécutent donc en quelques secondes.
- **Ancrage calendaire.** L'exécution est ancrée à une date de début. Chaque instant simulé correspond à une date et heure réelles, et toutes les dates des rapports sont exprimées en termes calendaires.

---

## 2. Pièces et modèles

Une **pièce** est un article individuel circulant sur la ligne. Elle est créée par le générateur, dotée de son propre identifiant, et suivie individuellement jusqu'à ce qu'elle termine dans un buffer de sortie ou de rebut.

Un **modèle** est le type d'une pièce, comparable à une référence produit. Deux pièces d'un même modèle partagent la même configuration (routes, durées, tailles de lot) et sont traitées de la même façon, mais chacune reste un article distinct doté de son propre identifiant. Des modèles distincts peuvent suivre des routes différentes et avoir des paramètres de traitement différents.

Les modèles forment une hiérarchie. Un modèle peut déclarer un **parent**, et tout composant configuré pour accepter un modèle accepte également l'ensemble de ses descendants. Cela permet une configuration commune au niveau de la famille, avec des surcharges par variante lorsque nécessaire.

```mermaid
flowchart TD
    M88[M88] --> M88s[M88 standard]
    M88 --> M88r[M88 renforcé]
```

Les modèles sans enfants sont les **modèles feuilles**. Les générateurs ne produisent que des modèles feuilles ; les modèles parents servent à désigner des groupes dans la configuration.

---

## 3. Les outlets : buffers et routeurs

Les composants déposent les pièces dans des **outlets**. Il en existe deux sortes : les buffers et les routeurs.

### Les buffers

Un **buffer** est une file dans laquelle les pièces attendent entre deux opérations. Chaque buffer déclare un ensemble de **modèles valides** ; seules les pièces de ces modèles (ou de leurs descendants) peuvent y entrer.

Un buffer possède l'un de trois types :

| Type | Rôle | Comportement |
|---|---|---|
| Passage | File intermédiaire | Les pièces attendent qu'un poste aval les collecte |
| Sortie (Exit) | Terminal, production | Les pièces sont comptées comme produites ; exactement un buffer de sortie par flux |
| Rebut (Scrap) | Terminal, rejet | Les pièces sont éliminées |

Les buffers de sortie et de rebut sont terminaux : les pièces n'en repartent jamais.

### Les routeurs

Un **routeur** répartit les pièces entrantes entre plusieurs buffers de destination selon des probabilités. Le routage est instantané ; un routeur ne retient aucune pièce. L'application typique est le tri qualité après une étape d'inspection.

```mermaid
flowchart LR
    T[Inspection] --> R{Routeur}
    R -->|0.95| OK[Accepté]
    R -->|0.05| SC[Rebut]
```

Une branche peut être désignée **freeloader**. Sa probabilité n'est pas spécifiée explicitement ; elle reçoit le reliquat des autres branches, ce qui garantit un total de 1 et reste correct lorsque les autres probabilités sont modifiées.

Les probabilités de branche peuvent être des constantes ou des fonctions du temps (voir section 10), ce qui permet de modéliser des taux dérivants, par exemple un taux de rebut qui augmente avec l'usure de l'outillage.

---

## 4. Les ressources

Une **ressource** est une matière consommable ou un équipement réutilisable (cire liquide, barbotine, moules). Les postes peuvent requérir des ressources pour opérer.

Propriétés :

- **Capacité** et **quantité initiale**.
- **Durée de vie.** La durée d'utilisabilité d'une unité. Une durée de vie infinie désactive la péremption ; une durée finie modélise une matière périssable.

Une **ressource réapprovisionnable** se recommande automatiquement. Lorsque le stock passe sous son **seuil**, une commande est passée ; après écoulement de la **durée de commande** puis de la **durée de livraison**, le stock est remis à capacité.

> **Note.** Un poste qui requiert une ressource épuisée attend. Pendant la durée de commande, le carrier concerné patiente et les opérateurs déjà réquisitionnés pour lui restent réservés (ils ne repartent pas travailler ailleurs). Cette attente apparaît dans les rapports comme attente matière, délais de recommande inclus.

### Le scope ressource

Le **scope ressource** définit comment un poste dimensionne la quantité de ressources qu'il consomme :

- **Par lot (per batch).** Une quantité fixe par lot, indépendante du nombre de pièces.
- **Par unité (per unit).** Une quantité proportionnelle au nombre de pièces du lot.

Le scope ressource ne peut pas être par tâche.

---

## 5. Les opérateurs

Un **groupe d'opérateurs** représente une équipe de travailleurs interchangeables.

- Le groupe possède un **effectif**, un ensemble de **shifts** définissant ses heures de travail, et un facteur de **productivité** qui met à l'échelle la vitesse des opérations (1,0 est nominal ; les valeurs peuvent être des lois).
- Hors de ses shifts, le groupe est indisponible, et les stations qui le requièrent attendent son retour.

Les postes référencent les opérateurs par des **alternatives** : une liste ordonnée de groupes acceptables. La première alternative disposant d'un personnel suffisant est utilisée. Les alternatives modélisent la polyvalence et les remplacements. Tous les opérateurs d'une même alternative doivent partager la même productivité.

Des opérateurs peuvent être requis à trois moments du traitement d'un lot, chacun avec ses propres alternatives : **opérateurs de mise en route**, **opérateurs de chargement**, et **opérateurs de traitement**.

> **Note.** L'attribution des opérateurs ne tient pas compte de la priorité des postes (section 7). Lorsque plusieurs stations réclament la même équipe au même instant, le personnel est attribué dans l'ordre des demandes, sans favoriser la station la plus prioritaire.

### Le scope opérateur

Le **scope opérateur** définit la durée pendant laquelle un poste retient son personnel :

- **Par lot (per batch).** Les opérateurs sont demandés pour un travail précis (charger un lot, traiter un lot) et libérés à la fin de ce travail. Le personnel circule librement entre les stations.
- **Par tâche (per task).** Le poste réquisitionne une équipe et la conserve à travers les lots successifs, la libérant lorsque le poste reste inactif au-delà de la borne de shift de l'équipe ou s'arrête. Cela représente du personnel posté à une station pour un shift.

La distinction se reflète dans la comptabilité de main-d'oeuvre : une équipe par tâche est comptée occupée pendant toute son affectation, intervalles d'inactivité entre lots compris, tandis qu'une équipe par lot n'est comptée que pendant ses travaux.

Le scope opérateur ne peut pas être par unité, et le scope ressource ne peut pas être par tâche ; ces combinaisons sont rejetées au chargement.

---

## 6. Les postes

Un **poste** est une station de travail. Il en existe deux sortes, distinguées par ce sur quoi elles opèrent :

- Un **poste à pièces** traite des pièces : il les collecte dans des buffers d'entrée, effectue une opération, et les dépose dans des buffers de sortie.
- Un **poste à ressources** transforme des matières : il consomme des ressources d'entrée et produit des ressources de sortie. Aucune pièce individuelle ne le traverse.

Les postes à pièces constituent l'essentiel d'un flux typique ; les postes à ressources fournissent les consommables. Les sections suivantes décrivent d'abord les postes à pièces ; la section 9 couvre les spécificités des postes à ressources.

### Les carriers

Les postes traitent les pièces par lots. L'unité de traitement par lot est le **carrier** : un conteneur logique, comparable à un plateau ou à une grille de four, qui rassemble un groupe de pièces, les maintient pendant l'opération, et les dépose en sortie.

```mermaid
flowchart LR
    IN[Buffers d'entrée] -->|collecte| C[Carrier]
    C --> W[Opération sur le lot]
    W --> OUT[Buffers de sortie]
```

Un poste peut faire tourner plusieurs carriers simultanément, dans la limite de ses réglages de capacité. Cela représente les stations où plusieurs lots sont en cours en même temps, telles que les zones de séchage ou de stockage.

### Cycle de vie d'un carrier

Chaque carrier traverse les mêmes étapes. Les rapports d'exécution mesurent le temps passé dans chacune, ce cycle est donc la base de l'interprétation des indicateurs de poste.

1. **Collecte.** Le carrier rassemble des pièces depuis les buffers d'entrée jusqu'à satisfaire ses exigences de lot ou jusqu'à expiration de son timeout.
2. **Chargement.** Le lot est chargé sur la station. Le chargement prend du temps et peut requérir des opérateurs.
3. **Traitement.** L'opération elle-même. Sa durée peut dépendre du modèle et peut requérir des opérateurs et des ressources.
4. **Dépôt.** Les pièces terminées sont placées dans les buffers de sortie.

> **Note.** La **mise en route** (préparation de la station : préchauffage, réglage) n'appartient pas au cycle d'un carrier. Elle est effectuée par la station elle-même, au démarrage, après toute interruption, et au début de chaque shift, avant qu'un carrier ne soit constitué. Elle est néanmoins mesurée et rapportée par poste (colonne `mise_en_route`).

Si la station est interrompue pendant le cycle (panne, arrêt programmé, fin de shift), le carrier peut être abandonné et ses pièces renvoyées dans un buffer, selon les protocoles configurés (section 12).

### Les collecteurs

Le **collecteur** est le composant d'un carrier qui effectue l'étape de collecte : il sélectionne les pièces à prendre et détermine quand cesser d'attendre. Le comportement du collecteur est configurable et décrit en section 8.

---

## 7. Configuration d'un poste

Cette section définit chaque réglage d'un poste à pièces.

### Taille de lot (par modèle)

- **Capacité minimale du carrier.** Le plus petit lot que le carrier accepte avant de procéder. La valeur 1 autorise le fonctionnement à la pièce.
- **Capacité maximale du carrier.** Le plus grand lot que le carrier contient.

Une station qui traite toujours des grilles pleines de 4 utilise minimum = maximum = 4. Une station qui démarre avec ce qui est disponible, jusqu'à 4, utilise minimum = 1 et maximum = 4.

### Capacité de la station

- **Capacité max.** Le nombre total de places-pièces de la station, partagé par tous les carriers simultanés. Ce réglage détermine le degré de parallélisme : avec une capacité max de 4 et des carriers de 4, un seul carrier tourne à la fois ; avec 40, jusqu'à dix carriers de ce type tournent en parallèle.
- **Carriers minimum.** Le nombre de carriers qui doivent être prêts avant qu'aucun ne se lance, formant une vague. La valeur habituelle est 1.

La capacité max doit suffire aux exigences de lot d'un carrier ; sinon les carriers ne peuvent jamais constituer leur lot et la station se bloque. Le Flow Designer valide cette contrainte.

### Indicateurs de comportement des carriers

- **Carriers contigus.** Détermine la réservation des places. Désactivé, un carrier réserve son empreinte maximale complète pendant la collecte, rendant ces places indisponibles pour les autres. Activé, un carrier n'occupe que les places correspondant aux pièces effectivement détenues.

  > **Exemple.** Une zone de séchage de 40 places alimentée par des carriers de 4 pièces. Carriers contigus activés : un carrier en cours de collecte n'occupe que les places de ses pièces déjà prises, laissant les autres libres pour d'autres carriers, ce qui remplit la zone au mieux. Carriers contigus désactivés : chaque carrier bloque d'emblée ses 4 places, même à moitié rempli, réservant la capacité mais la sous-utilisant pendant la collecte.

- **Carriers indépendants.** Détermine la synchronisation. Des carriers indépendants déroulent leurs cycles sur des chronologies séparées ; des carriers non indépendants avancent ensemble.

  > **Exemple.** Des carriers indépendants avancent chacun à leur rythme : un plateau peut sortir du four pendant qu'un autre y entre. Des carriers non indépendants forment une vague synchrone : tous démarrent et se terminent ensemble, comme les alvéoles d'un même moule partageant un cycle unique.

Les stations ordinaires à lot unique peuvent laisser ces deux réglages à leurs valeurs par défaut. Ils concernent principalement les zones de stockage et d'attente parallèles.

### Les durées

Trois durées, chacune spécifiée comme une loi de probabilité (section 10) :

- **Durée de mise en route.** Temps de préparation, effectué une fois au (re)démarrage de la station, pas à chaque lot.
- **Durée de chargement.** Temps de chargement du lot.
- **Durée de traitement.** Temps d'opération, configuré par modèle.

### Le timeout

Le **timeout** borne l'étape de collecte. À son expiration, le carrier procède avec les pièces collectées ; s'il n'en détient aucune, il continue d'attendre au moins une pièce. Un timeout infini signifie que le carrier attend indéfiniment son lot minimum.

> **Avertissement.** Le timeout s'évalue au sein d'une tentative de collecte active. Si la station sort de son shift, la tentative est interrompue et le timeout repart à la tentative suivante. Un timeout plus long que la fenêtre de travail de la station peut donc ne jamais expirer. Pour évacuer des lots partiels, choisissez un timeout plus court que le shift pendant lequel la station opère.

### La priorité

Un entier de 0 à 10 ; 10 est le plus élevé. Lorsque plusieurs postes se disputent la même entité rare (places, pièces) au même instant, le poste le plus prioritaire est servi en premier.

> **Note.** La priorité arbitre la compétition pour les places et les pièces. Elle ne s'applique pas aux groupes d'opérateurs : le personnel est attribué sans considération de priorité, dans l'ordre des demandes. Lorsque l'accès d'une station à du personnel partagé est critique, l'approche fiable est un groupe d'opérateurs dédié plutôt que partagé.

### Le drapeau Admin

Marque le poste comme **administratif** (contrôle, attente, rétention, stockage) plutôt que productif. Ce drapeau n'a aucun effet sur le comportement de la simulation ; il détermine uniquement le regroupement du poste dans le rapport de synthèse administratif contre productif (voir la [référence des KPI](kpis.fr.md)).

---

## 8. Les types de collecteurs

Le comportement du collecteur combine deux choix indépendants.

**Greedy contre altruiste** régit le moment où le collecteur réserve les pièces :

- Un collecteur **greedy** réserve les pièces au fil de l'eau, une par une, dès qu'elles deviennent disponibles, sans attendre qu'un lot minimum entier soit présent. Il peut ainsi accaparer des pièces et ralentir d'autres collecteurs travaillant en parallèle.
- Un collecteur **altruiste** attend qu'au moins un lot minimum de pièces soit disponible avant de les réserver. Il laisse ainsi la chance à d'autres collecteurs, dont le lot minimum est plus petit, de se servir en premier.

Dans les deux cas, une fois le lot minimum atteint, le collecteur complète vers le maximum avec les pièces immédiatement disponibles, puis procède.

**Discriminant contre non discriminant** régit la sélection de modèle :

- Un collecteur **non discriminant** accepte toute pièce valide et peut mélanger les modèles dans un lot. Cela exige que tous les modèles acceptés partagent la même durée de traitement et les mêmes tailles de lot, le lot étant traité comme une unité.
- Un collecteur **discriminant** sélectionne un modèle focus par lot et ne collecte que ce modèle.

Les quatre combinaisons de ces choix sont les quatre types de collecteurs.

Un collecteur discriminant sélectionne son modèle focus selon une règle configurable (cette règle n'a d'effet que pour un collecteur discriminant) :

- **Le plus présent.** Le modèle ayant le plus de pièces en attente.
- **La durée de traitement la plus courte.** Le modèle au traitement le plus rapide.
- **Le plus petit écart à la capacité minimale.** Le modèle le plus proche de remplir son lot minimum.

Au sein du focus, les pièces individuelles sont sélectionnées selon l'**ordre de sortie des pièces** : **premier entré, premier sorti** (attente la plus longue dans le buffer) ou **premier créé, premier sorti** (date de création la plus ancienne).

---

## 9. Les postes à ressources

Un poste à ressources transforme des matières. Ses réglages spécifiques :

- **Ressources non transformées.** Matières qui doivent être présentes et sont consommées à l'opération, mais qui n'entrent pas dans la composition de la ressource de sortie. Exemples : électricité, consommables ou fluides utilisés par la machine.
- **Ressources transformées.** Matières consommées en entrée, chacune avec une **proportion** définissant sa part du mélange. Les proportions décrivent une recette et totalisent 1.
- **Récupérable (salvageable).** Par ressource transformée : indique si la quantité réservée mais non consommée est récupérée (rendue au stock) plutôt que perdue lorsque le carrier est abandonné ou lors du rééquilibrage du mélange.
- **Ressources de sortie.** Pour chaque ressource produite, une loi bornée définit un coefficient (positif) appliqué à la quantité d'entrée consommée par le carrier. La quantité produite est donc proportionnelle, à un facteur aléatoire près, à la quantité de ressources transformées consommées.

Les opérateurs, durées, shifts et interruptions se comportent comme pour les postes à pièces. Les postes à ressources utilisent un collecteur simplifié avec le seul choix greedy contre altruiste.

---

## 10. Lois, fonctions du temps, et reproductibilité

La plupart des paramètres numériques acceptent une **loi de probabilité** plutôt qu'une valeur fixe :

| Loi | Caractéristiques | Usage typique |
|---|---|---|
| Constant | Valeur fixe | Durées exactes |
| Uniform | Équiprobable dans [bas, haut] | Incertitude bornée |
| Normal | Cloche autour d'une moyenne | Variation naturelle |
| Exponential | Beaucoup de valeurs courtes, peu de longues | Temps inter-événements |
| Triangular | Bas, mode, haut | Estimations à trois points |
| LogNormal | Asymétrique à droite, positive | Durées parfois très longues |

Certains paramètres acceptent en outre des **fonctions du temps** : des valeurs qui évoluent au fil de l'exécution. Elles servent notamment à modéliser une montée en cadence (ramp-up), une durée ou une probabilité qui évolue en début d'horizon, ou un taux de rebut dérivant. De plus, les paramètres d'une loi de probabilité peuvent eux-mêmes être des fonctions du temps : par exemple, la moyenne d'une loi Normale peut décroître au fil du run.

| Fonction du temps | Forme | Usage typique |
|---|---|---|
| Linéaire | Variation à pente constante entre deux points | Montée en cadence progressive |
| Palier (step) | Constante par paliers, avançant d'un cran à intervalle fixe | Changements discrets périodiques |
| Exponentielle | Approche asymptotique d'une limite | Montée ou décroissance qui sature |

Chaque exécution utilise une **graine (seed)** qui initialise le générateur de nombres aléatoires. Graine et modèle identiques produisent une exécution identique sur un même moteur ; changer la graine donne une réalisation indépendante. Utilisez une graine fixe pour la reproductibilité et plusieurs graines pour évaluer la variabilité.

> **Note.** Une même graine ne reproduit une exécution identique que pour un moteur donné. Les moteurs Python et C++ n'emploient pas le même générateur de nombres aléatoires ; à graine égale, ils produisent des réalisations différentes mais statistiquement comparables.

---

## 11. Les shifts et le calendrier

Un **shift** définit les heures de travail d'un poste, d'un générateur, ou d'un groupe d'opérateurs. Hors de ses shifts, l'entité est inactive.

> **Note.** Les shifts d'un poste et du générateur de pièces représentent leur **temps d'ouverture** : les périodes durant lesquelles la station est ouverte ou le générateur émet. Ils sont distincts des shifts d'un groupe d'opérateurs, qui représentent les heures de présence du personnel. Un poste ouvert dont les opérateurs sont hors shift attend son équipe.

Deux modes de définition :

- **Hebdomadaire (weekly).** Un motif hebdomadaire répété, appliqué sur une plage de dates.
- **Personnalisé (custom).** Des intervalles date-heure explicites.

Les deux modes acceptent des **jours de fermeture (days off)** : des dates du calendrier, tirées d'un registre partagé, auxquelles le planning ne s'applique pas.

Les shifts sont le lien principal entre le modèle et le calendrier. Lorsque la production cale ou qu'une entité paraît sous-utilisée, la configuration des shifts est le premier élément à vérifier.

---

## 12. Les interruptions

### Les pannes (breakdowns)

Une **panne** est une défaillance aléatoire non planifiée, caractérisée par un **temps moyen entre pannes (MTBF)** et un **temps moyen de réparation (MTTR)**.

Le MTBF se spécifie de deux façons :

- Par une **loi de probabilité** (le temps jusqu'à la panne suivante est tiré de cette loi).
- Par une **courbe en baignoire (bathtub curve)** du taux de défaillance. Le taux de défaillance est le taux instantané de pannes ; il est élevé au rodage (mortalité infantile), bas et à peu près constant pendant la vie utile, puis croissant à l'usure. Le temps jusqu'à la panne suivante est tiré du processus de Poisson correspondant à ce taux. La courbe prend cinq paramètres :

| Paramètre | Rôle |
|---|---|
| a | Amplitude du terme de rodage : la hauteur initiale du taux de défaillance au démarrage. |
| tau | Constante de temps du rodage : la vitesse à laquelle la mortalité infantile décroît. |
| c | Niveau de base constant : le fond plat de la baignoire, les pannes aléatoires de la vie utile. |
| beta | Forme du terme d'usure (Weibull) : la raideur de la remontée en fin de vie. |
| eta | Échelle du terme d'usure : la vie caractéristique, où l'usure s'installe. |

Lors d'une défaillance, le travail en cours est interrompu. Pour un poste à pièces, les pièces en cours sont déposées dans des **outlets canots de sauvetage** désignés plutôt que perdues. La station reprend après réparation.

### Les arrêts programmés (shutdowns)

Un **arrêt programmé** est un arrêt planifié (maintenance, nettoyage). Deux variantes :

- **Non flexible.** A lieu exactement comme planifié ; le travail en cours est interrompu.
- **Flexible.** Peut glisser légèrement pour permettre au lot en cours de se terminer avant l'arrêt.

Les arrêts sont spécifiés soit comme intervalles explicites, soit générés périodiquement (intervalle, durée, plage de dates).

En termes de reporting, les arrêts programmés sont des pertes planifiées, déduites du temps requis avant le calcul de la disponibilité, tandis que les pannes sont des pertes non planifiées qui réduisent la disponibilité. Voir la [référence des KPI](kpis.fr.md).

---

## 13. Le générateur de pièces

Chaque flux contient exactement un **générateur de pièces**, la source de toutes les pièces. Il émet pendant ses propres shifts, vers ses buffers de destination configurés. Le régime d'émission est déterminé par le critère d'arrêt (section 14) et fonctionne selon l'un de deux modes.

### Le mode objectifs

Chaque modèle feuille reçoit un objectif de bonnes pièces. Le générateur cadence l'émission par un **gap** (l'intervalle entre deux pièces créées), fixé manuellement ou calculé automatiquement à partir de l'objectif total et du temps de travail disponible.

- **La période de grâce.** Avec un gap automatique, une période de grâce peut être réservée : une portion du temps de travail en fin d'horizon exclue du calcul de cadence. Elle fournit du mou pour que la ligne se vide et que les pièces rebutées soient refaites avant l'échéance.
- **La refabrication consciente du rebut.** Le générateur surveille le rebut. Une pièce rebutée laisse son objectif non satisfait, et le générateur émet un remplacement. Les objectifs s'expriment donc en bonnes pièces livrées ; le nombre de pièces injectées peut dépasser l'objectif du nombre de rebuts.

> **Note.** La période de grâce fonctionne comme un budget de refabrication. Chaque refabrication en consomme une part ; un taux de rebut élevé peut donc épuiser la période de grâce avant l'achèvement de tous les remplacements, terminant l'exécution en deçà de son objectif. Dimensionnez la période de grâce selon le nombre de rebuts attendu.

### Le mode débit

Le générateur émet à un **gap** spécifié (éventuellement une fonction du temps) avec un **mélange de modèles** donnant la part de chaque modèle. Un modèle peut être le freeloader, recevant la part résiduelle. Le mode débit sert à l'étude d'une ligne sous un flux d'entrée donné, sans objectifs de production.

---

## 14. Les critères d'arrêt

Le **critère d'arrêt** termine l'exécution.

- **Par le temps (by time).** L'exécution se termine à une date spécifiée. Utilisé avec le mode débit.
- **Par pièces produites (by pieces produced).** L'exécution se termine lorsque le buffer de sortie atteint l'objectif total. Utilisé avec le mode objectifs. Un **timeout** fournit une borne supérieure : si l'objectif n'est pas atteint au timeout, l'exécution se termine et les rapports reflètent le résultat partiel.

> **Note.** Lorsque le timeout est infini, la fin de l'exécution est déterminée par l'atteinte de l'objectif : le critère détecte que le buffer de sortie a atteint le total visé et termine le run. Ce n'est ni une durée fixe, ni l'épuisement des événements (le générateur continue d'émettre tant que l'objectif n'est pas atteint). Un garde-fou s'ajoute : si aucune pièce n'atteint la sortie pendant une longue période de temps simulé, l'exécution s'arrête avec une erreur explicite plutôt que de tourner indéfiniment. C'est une soupape de sécurité contre un run bloqué, pas le mécanisme de fin normal.

---

## 15. Relations entre composants

```mermaid
classDiagram
    class Modele
    class Piece
    class Buffer
    class Routeur
    class Poste
    class PosteAPieces
    class PosteARessources
    class Carrier
    class Collecteur
    class GroupeOperateurs
    class Ressource
    class Shift
    class Panne
    class Arret
    class Generateur
    class CritereArret

    Piece --> Modele : a pour type
    Modele --> Modele : parent de
    Buffer --> Modele : accepte
    Routeur --> Buffer : route vers
    Poste <|-- PosteAPieces
    Poste <|-- PosteARessources
    PosteAPieces --> Carrier : fait tourner
    Carrier --> Collecteur : collecte via
    PosteAPieces --> Buffer : consomme et alimente
    Poste --> GroupeOperateurs : requiert
    Poste --> Ressource : consomme
    Poste --> Shift : opère pendant
    Poste --> Panne : sujet à
    Poste --> Arret : sujet à
    Generateur --> Piece : émet
    Generateur --> Modele : produit
    CritereArret --> Generateur : cadence et termine
```

Cycle de vie d'une pièce :

```mermaid
flowchart TD
    A[Le générateur émet une pièce] --> B[La pièce entre dans un buffer valide]
    B --> C[Un carrier la collecte dans un lot]
    C --> D[Le lot est chargé et traité]
    D --> E{Routeur}
    E -->|conforme| F[Buffer suivant ou sortie]
    E -->|non conforme| G[Rebut ; le générateur émet un remplacement]
    F --> H[Sortie : comptée comme produite]
```

---

## Pour aller plus loin

- Construire et lancer des modèles : [guide du Flow Designer](flow-designer.fr.md).
- Interpréter les sorties d'exécution : [référence des KPI](kpis.fr.md).
