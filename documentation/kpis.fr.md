# Référence des KPI

Chaque run produit un dossier de résultats contenant des rapports CSV (lisibles directement dans Excel) et un ensemble de graphiques. Ce document définit chaque fichier et chaque indicateur : ce qu'il mesure, comment il est calculé, et les points demandant de la vigilance à l'interprétation.

**Prérequis :** la [référence de la simulation](simulation.fr.md), dont le vocabulaire (pièce, tâche, carrier, buffer, opérateur, scope, admin) est utilisé sans être redéfini. Le [guide du Flow Designer](flow-designer.fr.md) décrit la production des runs.

Propriétés générales des rapports :

- Tous les indicateurs sont collectés à chaque run, pour chaque composant. Aucune activation n'est nécessaire.
- Les moteurs Python et C++ produisent des fichiers identiques, de structure identique. Le choix du moteur n'affecte que la vitesse d'exécution (ET PEUT ETRE LES RESULTATS CAR RANDOM DE C++ EST DIFFERENT DE RANDOM DE PYTHON).

---
(SECTIONS 'FORMATS' INUTILE - A ENLEVER)
## Formats

- **Durées :** `1h 10m`, `3j 5h 20m` (`j` désigne les jours), `3m 20s` en dessous d'une heure. En interne, tous les temps sont des minutes simulées ; seule la présentation varie.
- **Taux :** pourcentages (`8.1%`, `83%`).
- **Instants** (création et achèvement des pièces) : dates calendaires réelles (`05-01-2026 14:05`), dérivées de la date de début du run.
- **Flux :** pièces par jour (colonnes suffixées `_j`).

---

## La cascade des temps

```
temps total (TT)                        toute la durée simulée
└─ temps d'ouverture (TO)               les shifts du poste
   └─ temps requis (TR)                 TO moins les arrêts programmés
      └─ temps de fonctionnement (TF)   au moins un lot actif sur le poste
         └─ temps à valeur ajoutée      chargement et traitement effectifs
            └─ temps net (TN)           reconstruit : cycle idéal x pièces produites
```

Deux définitions demandent de l'attention.

**Le temps net est reconstruit, pas mesuré.** TN est le temps qu'aurait pris la production réelle du poste à la cadence nominale. Comparé au temps à valeur ajoutée effectif, il donne le taux de performance ; l'écart représente les pertes de cadence (cycles lents, lots partiels).

**Le temps de cycle idéal** est le temps théorique de production d'une pièce dans des conditions parfaites : lot plein, cadence nominale, aucune attente. Exemple, un four :

- temps de traitement configuré : 120 minutes par lot (moyenne),
- chargement : 10 minutes,
- lot maximum : 4 pièces.

Un lot plein demande 130 minutes et livre 4 pièces : cycle idéal = 130 / 4 = 32.5 minutes par pièce ; cadence nominale = 60 / 32.5, environ 1.85 pièce par heure.

Les durées configurées étant des distributions, la référence utilise leur moyenne (évaluée à t = 0 quand les paramètres varient dans le temps). Il s'agit d'une convention de référence, analogue à la cadence nominale d'une fiche machine, pas d'une mesure. Le cycle idéal sert exclusivement à construire TN, et par là la performance et le TRS. Chaque modèle a son propre cycle idéal (`tc_ideal` dans `postes_modeles.csv`).

---

## postes.csv, un poste par ligne

La colonne `admin` (oui/non) reflète le marqueur admin de la tâche. Elle n'a aucun effet sur la simulation ; elle détermine le regroupement dans `synthese_admin.csv`.

### Colonnes de temps (`temps_*`, `arrets_programmes`, `pannes`, `gel`, `mise_en_route`)

- `temps_total` : toute la durée simulée.
- `temps_ouverture` : temps passé dans les shifts du poste, mesuré sur l'état interne de son calendrier (correct à travers minuit et les jours fériés).
- `arrets_programmes` : arrêts programmés tels que réellement pris. Un arrêt flexible ayant glissé pour laisser un lot se terminer est compté là où il a eu lieu.
- `temps_requis` : ouverture moins arrêts programmés. Le dénominateur du TRS.
- `pannes`, `nb_pannes`, `mtbf`, `mttr` : temps total de panne, nombre de pannes, temps moyen entre débuts de panne, durée moyenne de réparation.

  > **Note.** Les pannes sont mesurées sur tout l'horizon et peuvent chevaucher des périodes hors horaires ; `pannes` peut donc dépasser ce que la cascade suggère. Le MTBF n'est rapporté qu'à partir de deux pannes observées.

- `gel` : temps gelé **pendant les heures d'ouverture** : le poste n'a pas pu terminer un lot avant un départ d'équipe ou un arrêt. Le poste reprend au retour de l'équipe concernée, pas seulement à son propre shift suivant, ce qui borne le temps gelé. La fermeture (nuits, week-ends) n'est pas comptée comme temps gelé. (ON PEUT EXPLIQUER MIEUX C'EST QUOI LE TEMPS DE GEL: C'EST LE TEMPS OU LA MACHINE PEUT THEORIQUEMENT FONCTIONNER, MAIS NE FONCTIONNE PAR CAR ELLE ANTICIPE SOIT UN ARRET DE SON PROPRE SHIFT, SOIT UN ARRET DE SHIFT DE SES OPERATEURS, SOIT UN ARRET PLANIFIE)
- `mise_en_route`, `nb_mises_en_route` : temps total de mise en route et nombre de mises en route. Le poste redémarre après chaque interruption et à chaque nouveau shift. Il s'agit de la durée de mise en route configurée elle-même ; l'attente de l'équipe de mise en route est une perte de disponibilité au sein du temps de fonctionnement, pas une composante de cette colonne.
- `temps_fonctionnement` : temps avec au moins un lot actif (ACTIF INCLUS QUOI? LE TEMPS D'ATTENTE DES OPERATEURS ? DES RESSOURCES ? DE PLACES DISPONIBLES AKA VACANT SLOTS? TEMPS DE REAPPRO? OU BIEN ACTIF C.A.D LANCE AKA TOUTES LES CONDITIONS SONT FOURNIES ? DETAILLE UN PEU). Le TF de la cascade.

### Colonnes de taux (`taux_de_charge` à `tre`)

- `taux_de_charge` = TR / TO : la part engagée du temps d'ouverture.
- `disponibilite` = TF / TR : la disponibilité. Pertes : pannes, mises en route, attente de l'équipe de mise en route, temps gelé, et famine (aucune pièce disponible). Une disponibilité basse sur un poste affamé reflète la réalité, pas une erreur de mesure.
- `performance` = TN / (temps de chargement + temps de traitement) : l'efficacité de cadence en fonctionnement. Pertes : cycles plus lents que le nominal, productivité des équipes, lots partiels. Le temps à valeur ajoutée est sommé sur tous les lots plutôt que divisé par TF parce qu'un poste peut traiter des lots en parallèle ; la sommation maintient la performance dans [0, 100%].
- `qualite` = bonnes / produites. Les bonnes pièces d'un poste sont celles que son router aval immédiat n'a pas envoyées au rebut ; sans route de rebut, la qualité vaut 1.
- `trs` = disponibilité x performance x qualité : le TRS, dans [0, 100%] (EXPLIQUE UN PEU COMMENT C'EST EQUIVALENT A LA FORMULE DE TRS = TEMPS UTILE / TEMPS REQUIS).
- `trg` = TRS x taux_de_charge : les arrêts programmés comptés comme pertes (EXPLIQUE COMMENT C'EST EQUIVALENT A TRG = TEMPS UTILE / TEMPS OUVERTURE).
- `tre` = TRS x (TR / TT) : tout le calendrier compté, périodes fermées incluses.

### Colonnes de production (`pieces_*`, `nb_lancements`, `taille_lot_moyenne`, `cycle_*`, `debit_pieces_j`, `flux_*`)

- `pieces_produites` : pièces déposées par les lots terminés. Les lots évacués par une interruption n'ont rien produit et ne sont pas comptés. Pour les tâches ressources, la quantité de matière transformée.
- `pieces_bonnes`, `pieces_rebutees` : répartition selon le verdict du router aval immédiat.
- `nb_lancements` : nombre de lots terminés. `taille_lot_moyenne` : taille moyenne des lots ; une moyenne nettement sous le maximum signale une perte de performance.
- `cycle_moyen`, `cycle_p90`, `cycle_max` : durée d'un lot de sa création (début de la collecte) à son dépôt. Le p90 se lit : 9 lots sur 10 se terminent dans ce délai.
- `debit_pieces_j` : pièces produites par jour de temps requis.
- `flux_entrant_j`, `flux_sortant_j` : pièces physiquement prélevées en entrée et déposées en sortie, par jour calendaire. Les re-collectes après évacuation comptent dans le flux entrant, en tant que flux physique. Un flux entrant durablement supérieur au flux sortant signale une accumulation.

### Colonnes d'attente (`attente_*`, `temps_collecte`, `temps_chargement`, `temps_traitement`)

Chaque lot étiquette son activité courante ; les étiquettes sont cumulées :

- `attente_pieces` : attente de pièces (famine amont).
- `attente_place` : attente de places libres (la capacité maximale du poste lui-même).
- `attente_operateurs` : attente d'une équipe.
- `attente_matiere` : attente de matière (délais de réapprovisionnement inclus).
- `attente_vague` : attente des autres carriers d'une vague (carriers minimum) (AJOUTE QUE PERTINENT QUE SI MIN_CARRIERS > 1).
- `temps_collecte` : temps d'assemblage des lots.
- `temps_chargement`, `temps_traitement` : chargement et traitement.

> **Note.** Ces colonnes se recouvrent partiellement (`temps_collecte` englobe les attentes de pièces et de place du collecteur) et les lots parallèles attendent simultanément.

### Colonnes d'heures (`heures_machine`, `heures_main_oeuvre`)

Deux colonnes comptables aux règles d'agrégation délibérément différentes :

- `heures_machine` : temps horloge pendant lequel la machine charge ou traite, agrégé en **union** sur les lots. Un poste est une machine physique : trois lots parallèles pendant 40 minutes contribuent 40 minutes machine. `heures_machine` diffère de TF : TF inclut les attentes d'un lot engagé, les heures machine non ; les heures machine sont donc au plus égales à TF, et l'écart vaut les attentes des lots engagés. Le temps de mise en route est exclu et rapporté dans `mise_en_route`. (ICI JE NE SAIS PAS SI DANS LES CSV ON AJOUTE UNE CASE 'HEURES MACHINES TOTALES'. SINON, AJOUTE LA DANS 'FLUX.CSV')
- `heures_main_oeuvre` : minutes opérateur réservées pour le poste par toutes ses équipes, agrégées en **somme** (opérateurs x durée). Le compte couvre les équipes de chargement et de traitement par lot pendant leurs jobs, l'équipe de mise en route pendant la mise en route, et les équipes par tâche sur toute leur affectation, intervalles d'inactivité inclus. (AJOUTE AUSSI HEURES_MAIN_OEUVRE DANS FLUX.CSV SI C'EST PAS DEJA FAIT). Le ratio `heures_main_oeuvre / heures_machine` exprime l'effectif moyen par heure machine.

---

## postes_modeles.csv, la production par modèle

Par tâche pièces et par modèle : le temps de cycle idéal (`tc_ideal`) (EST CE QUE C'EST LA MOYENNE? SI OUI DIS-LE) et les comptes produit, bon, rebuté. C'est le détail sous-jacent à TN.

---

## buffers.csv, un buffer par ligne

- `longueur_moyenne`, `longueur_max`, `longueur_ecart_type` : statistiques de longueur de file, pondérées par le temps. Un buffer qui gonfle signale un goulot immédiatement en aval.
- `longueur_finale` : le nombre de pièces restantes en fin de run.
- `sejour_moyen`, `sejour_max` : temps de séjour des pièces (vide pour les buffers de sortie et de rebut, qui sont terminaux).
- `entrees`, `sorties` : le trafic total, pièces collectées dès leur arrivée incluses.
- `flux_entrant_j`, `flux_sortant_j` : le même trafic par jour calendaire.
- `temps_moyen_entre_arrivees` : durée simulée / entrées.

---

## operateurs.csv, un groupe d'opérateurs par ligne

- `effectif` : la taille du groupe. `temps_poste` : le temps posté total (la somme des shifts du groupe sur le run).
- `occupation_moyenne` : l'effectif réquisitionné moyen sur toute la durée.
- `heures_en_poste` / `heures_hors_poste` : minutes opérateur réquisitionnées pendant et hors des shifts du groupe. Colonnes de diagnostic : les équipes par tâche sont libérées en fin de shift et sur abandon de lot, et l'adéquation au shift est revérifiée après les attentes de matière ; `heures_hors_poste` doit donc rester proche de zéro. Les valeurs résiduelles correspondent aux commandes de réapprovisionnement retenant une équipe au-delà de la borne du shift ou, sans contrainte de shift, à des lots se terminant légitimement après elle.
- `taux_occupation` : temps réquisitionné total / (effectif x temps posté), la part réquisitionnée du temps posté. Les valeurs restent sous 100% par construction, les équipes étant libérées en fin de shift (AJOUTE QUE THEORIQUEMENT CA POURRAIT DEPASSER 100% A CAUSE DES NON-CONTRAINTES DE SHIFT).
- `occupation_max` : le pic de réquisition simultanée.

---

## ressources.csv, une ressource par ligne

- `capacite` : la capacité de la ressource (COMBIEN ON PEUT STOCKER SIMULTANEMENT EN MEME TEMPS).
- `stock_moyen`, `stock_min`, `stock_max`, `stock_final` : statistiques du niveau de stock (moyenne pondérée par le temps) et niveau final.
- `consommation_totale`, `entrees_totales` : consommation totale et réapprovisionnement total.
- `consommation_j` : la consommation par jour calendaire.
- `nb_ruptures`, `temps_rupture` : nombre de ruptures (stock atteignant zéro) et temps total à zéro. Des ruptures récurrentes identifient la ressource qui affame ses tâches consommatrices.

---

## flux.csv et flux_modeles.csv, les indicateurs de ligne

- `sorties`, `rebuts`, `taux_rebut` : la production totale, le nombre de rebuts et le taux de rebut. Avec le générateur conscient des rebuts, les pièces rebutées sont relancées ; les objectifs s'expriment en bonnes pièces.
- `debit_sorties_j` : bonnes pièces par jour sur toute la durée.
- `traversee_*` : temps de traversée des pièces sorties, de la création à la sortie : moyenne, médiane, p90, max. Les mêmes statistiques par modèle figurent dans `flux_modeles.csv`.
- `encours_moyen`, `encours_max`, `encours_final` : l'en-cours : pièces créées mais ni sorties ni rebutées, qu'elles soient en buffer ou sur un poste. `encours_final` peut donc dépasser la somme des contenus des buffers.
- `flux_modeles.csv` par modèle : `objectif` (l'objectif du générateur), `genere` (pièces injectées, relances incluses), sorties, rebuts, `atteinte` = sorties / objectif, et les statistiques de traversée. `objectif` et `atteinte` ne sont renseignés qu'en mode objectif ; en mode cadence le générateur n'a pas d'objectif par modèle et ces colonnes restent vides.

---

## synthese_admin.csv, administratif contre productif

Une synthèse comparant les tâches marquées admin aux autres. Une ligne par indicateur ; les colonnes donnent la valeur cumulée de chaque groupe, le total, la part de chaque groupe (`part_admin`, `part_productif`, sommant à 100%) et le ratio `ratio_admin_productif`.

Les cinq indicateurs : nombre de postes, temps de fonctionnement, temps de cycle total (sommé sur les lots), heures machine, heures de main d'oeuvre.


---

## temps_traversee.csv, une ligne par pièce

L'enregistrement brut par pièce : pièce, modèle, issue (`sortie` ou `rebut`), dates de création et d'achèvement, temps de traversée. Adapté à l'analyse par tableau croisé dynamique, par modèle ou par période.

---

## graphes/, les graphiques

Chaque figure est fournie sous deux formes : le PNG rendu et les données tracées en CSV. L'arborescence sépare par format, puis par catégorie :

```
graphes/
    png/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
    csv/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
```

- `ressources/stock_*` : le niveau de stock au fil du temps.
- `buffers/longueur_*` : la longueur des buffers au fil du temps.
- `ligne/pieces_en_attente` : la longueur totale des buffers de passage ; `ligne/encours` : l'en-cours au fil du temps.
- `postes/occupation_*` : les places occupées au fil du temps (occupé = capacité moins vacant) ; la capacité du poste figure dans le titre. Avec les carriers contigus désactivés, les places réservées par un lot démarré comptent comme occupées.
- `operateurs/disponibles_*` : les opérateurs disponibles par groupe au fil du temps (zéro hors shifts par construction).
- `modeles/trajectoires_<modele>` : les routes observées du modèle, une barre par trajectoire distincte, ordonnées par fréquence, annotées des comptes et des parts. Chaque barre empile les étapes dans l'ordre ; la longueur d'un segment est le temps moyen à cette étape (bleu : attente en buffer ; orange : poste). Seules les pièces terminées (sortie ou rebut) sont incluses.
- `modeles/production` : par modèle, en mode objectif trois barres (objectif, généré relances incluses, produit) ; en mode cadence deux barres (généré, produit).

> **Note.** Sur les très gros runs, les séries temporelles extrêmement longues sont sous-échantillonnées à l'écriture des données de graphique, en préservant la forme de l'enveloppe (pics et creux) tout en omettant certains points intermédiaires. Cela ne concerne que les données tracées ; les valeurs des rapports CSV ne sont pas affectées.

---

## run.csv, l'identité du run

Fichier source, dates calendaires de début et de fin, durée simulée, graine aléatoire, horodatage de génération, temps de calcul (la durée d'exécution réelle) et le critère d'arrêt avec ses paramètres (`critere_arret`, `critere_details`). Une graine et un fichier de modèle identiques reproduisent des CSV identiques.
