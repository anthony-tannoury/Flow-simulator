# Les sorties d'une exécution et les KPI, expliqués

Chaque exécution écrit un dossier de résultats : des fichiers CSV que vous ouvrez directement dans Excel, plus un dossier de graphiques. Ce document explique ce que veut dire chaque nombre, comment il est mesuré, et les pièges de lecture.

Si ce n'est pas déjà fait, lisez d'abord le [guide de la simulation](simulation.fr.md). Ce document emploie son vocabulaire (pièce, poste, carrier, buffer, opérateur, scope, admin) sans le redéfinir. Le [guide du Flow Designer](flow-designer.fr.md) vous dit comment produire ces sorties ; celui-ci vous dit comment les lire.

Deux choses d'emblée :

- **Il n'y a rien à activer.** Chaque indicateur, pour tous les postes et tous les buffers, est mesuré à chaque exécution.
- **Les deux moteurs sont d'accord.** Que vous lanciez le moteur Python ou le moteur C++, vous obtenez les mêmes fichiers avec les mêmes colonnes et les mêmes nombres. Le choix du moteur ne concerne que la vitesse.

---

## Les formats

- **Durées** : `1h 10m`, `3j 5h 20m` (et `3m 20s` sous l'heure). En interne tout est en minutes de simulation ; seule la présentation change.
- **Taux** : en pourcentage (`8.1%`, `83%`).
- **Instants** (création et fin d'une pièce) : en date réelle du calendrier (`05-01-2026 14:05`), calculée depuis la date de début de la simulation.
- **Flux** : en pièces par jour (colonnes suffixées `_j`).

---

## La cascade des temps, la clé de lecture

Presque tous les indicateurs par poste découlent d'un même découpage du temps, celui de la norme NF E60-182. On part du calendrier et on retire des pertes, étage par étage :

```
temps total (TT)            toute la durée simulée
└─ temps d'ouverture (TO)   les horaires du poste (ses shifts)
   └─ temps requis (TR)     TO moins les arrêts programmés (maintenances)
      └─ temps de fonctionnement (TF)   il y a au moins un lot sur la machine
         └─ temps de valeur ajoutée      chargement + traitement réels
            └─ temps net (TN)            reconstruit : tc idéal x pièces produites
```

Deux choses importantes.

**TN n'est pas un temps d'horloge.** C'est un temps *reconstruit* : « produire ce que le poste a produit, à la cadence nominale, aurait dû prendre TN minutes ». On le compare au temps de valeur ajoutée réel (chargement + traitement) pour obtenir la performance ; l'écart, ce sont les pertes de cadence (cycles lents, lots incomplets).

**Le tc idéal (temps de cycle idéal), pas à pas.** C'est le temps qu'il *faudrait* au poste pour produire **une** pièce si tout se passait parfaitement : lot plein, cadence nominale, zéro attente. Exemple concret, un four de cuisson :

- durée de cuisson configurée : en moyenne 120 min par fournée,
- chargement : 10 min,
- taille maximale d'une fournée : 4 pièces.

Une fournée pleine coûte 120 + 10 = 130 minutes et sort 4 pièces, donc `tc idéal = 130 / 4 = 32,5 min par pièce`. La cadence nominale est son inverse, environ 1,85 pièce/heure.

Pourquoi « la moyenne » ? Parce que les durées configurées sont des lois aléatoires (Normal, Uniform, etc.) : pour définir une référence stable on prend leur moyenne (évaluée à t = 0 si les paramètres varient dans le temps). C'est une **convention de référence**, pas une mesure, exactement comme la cadence nominale affichée sur la fiche d'une machine réelle.

À quoi il sert : uniquement à construire TN (`tc x produites`), donc la performance et le TRS. Si le four tourne en fournées de 2 au lieu de 4, le temps machine est identique mais TN est divisé par deux : la performance chute, et le TRS montre la perte. Chaque modèle a sa durée et sa taille de lot, donc **son** tc idéal : c'est la colonne `tc_ideal` de `postes_modeles.csv`.

---

## postes.csv, un poste par ligne

C'est le grand rapport. Chaque ligne est un poste.

La colonne `admin` (oui/non) indique si le poste est **administratif** (contrôle, attente, prison, stockage) plutôt que productif. C'est une simple étiquette, réglée par le drapeau admin du poste dans le designer. Elle ne change rien à la simulation ; elle sert uniquement à la synthèse `synthese_admin.csv`.

### Les temps (`temps_*`, `arrets_programmes`, `pannes`, `gel`, `mise_en_route`)

- `temps_total` : la durée simulée totale.
- `temps_ouverture` : le temps passé dans les horaires du poste. Mesuré sur l'état interne « en horaire » du poste, donc exact même avec des shifts qui passent minuit ou des jours fériés.
- `arrets_programmes` : les arrêts planifiés réellement pris. Un nettoyage « flexible » qui a glissé pour laisser finir un lot est compté là où il a réellement eu lieu.
- `temps_requis` : ouverture moins arrêts programmés. Le dénominateur du TRS.
- `pannes`, `nb_pannes`, `mtbf`, `mttr` : temps total en panne, nombre de pannes, temps moyen entre deux débuts de panne, durée moyenne d'une réparation. **Piège :** les pannes sont mesurées sur tout l'horizon ; une panne peut chevaucher une période hors horaire, donc `pannes` peut dépasser ce que la cascade laisse imaginer. Le MTBF n'est affiché qu'à partir de deux pannes observées.
- `gel` : temps passé « figé » **pendant les heures d'ouverture** : le poste s'est figé (il ne pouvait pas finir un lot avant qu'une équipe ne quitte son poste, ou avant un arrêt). Il redémarre dès que l'équipe concernée revient (pas seulement au prochain shift du poste), donc le gel reste borné même si le shift du poste dure plusieurs jours. La fermeture (nuits, week-ends) n'est pas du gel et n'est pas comptée ici.
- `mise_en_route`, `nb_mises_en_route` : temps total et nombre de démarrages (chauffe, réglages) ; le poste redémarre après chaque interruption (panne, arrêt) **et à chaque nouveau shift**. C'est le temps de réglage lui-même (la durée configurée), **pas** l'attente de l'équipe de démarrage : cette attente est une perte de disponibilité, comptée dans le TF plus bas, pas ici.
- `temps_fonctionnement` : le temps avec au moins un lot actif sur le poste. C'est le TF de la cascade.

### Les taux (`taux_de_charge` vers `tre`)

- `taux_de_charge` = TR / TO. Quelle part de l'ouverture est réellement engagée (le reste part en arrêts programmés).
- `disponibilite` (Do) = TF / TR. Quand le poste devait tourner, a-t-il tourné ? Les pertes ici : pannes, démarrages, attente de l'équipe de démarrage, gel, et surtout la **famine** (pas de pièces à traiter). Un poste goulot très affamé aura un Do faible : c'est réel, pas une anomalie.
- `performance` (Tp) = TN / (temps de chargement + temps de traitement). Quand il tournait, tournait-il à la cadence nominale ? Les pertes : cycles plus lents que la moyenne, productivité des équipes, et surtout **lots incomplets** (un gabarit qui tourne avec 2 pièces sur 4 possibles). Le temps de valeur ajoutée est additionné sur **tous** les lots, pas divisé par le TF, parce qu'un poste peut traiter plusieurs lots **en parallèle** (carriers indépendants, zones d'attente/stockage). Additionner le temps de chaque lot garde Tp dans [0, 100 %].
- `qualite` (Tq) = bonnes / produites. Les « bonnes » pièces d'un poste sont celles que son trieur aval n'a pas envoyées au rebut ; un poste sans routage vers le rebut a Tq = 1.
- `trs` = Do x Tp x Tq. L'indicateur roi (disponibilité x performance x qualité), toujours dans [0, 100 %].
- `trg` = TRS x taux_de_charge : comme le TRS mais les arrêts programmés comptent en perte.
- `tre` = TRS x (TR / TT) : tout le calendrier compte, même les nuits fermées.

### La production (`pieces_*`, `nb_lancements`, `taille_lot_moyenne`, `cycle_*`, `debit_pieces_j`, `flux_*`)

- `pieces_produites` : pièces déposées en sortie par les lots menés à terme (les lots évacués par une panne ou un arrêt ne comptent pas : ils n'ont rien produit). Pour un poste à ressources, c'est la quantité de matière transformée.
- `pieces_bonnes`, `pieces_rebutees` : répartition de ces pièces selon le verdict du trieur aval immédiat.
- `nb_lancements` : nombre de lots menés à terme. `taille_lot_moyenne` : leur taille moyenne. Une taille moyenne loin du maximum = pertes de performance.
- `cycle_moyen`, `cycle_p90`, `cycle_max` : durée d'un lot, de sa création (début de collecte) au dépôt des pièces. Le p90 dit : « 9 lots sur 10 finissent en moins de X minutes ».
- `debit_pieces_j` : pièces produites par **jour de temps requis** (la cadence réelle du poste quand il est censé tourner).
- `flux_entrant_j` : pièces physiquement prises dans les buffers d'entrée, par jour calendaire (les reprises après évacuation comptent : c'est le flux physique). `flux_sortant_j` : pièces déposées en sortie, par jour calendaire. Un poste qui entre plus qu'il ne sort accumule ou évacue.

### Les attentes (`attente_*`, `temps_collecte`, `temps_chargement`, `temps_traitement`)

C'est le diagnostic du goulot : où part le temps qui manque à la disponibilité ? Chaque lot étiquette ce qu'il est en train de faire, et on additionne :

- `attente_pieces` : famine en amont, le collecteur attend des pièces.
- `attente_place` : le poste est plein, plus de place libre (sa capacité max).
- `attente_operateurs` : l'équipe demandée n'est pas disponible.
- `attente_matiere` : stock de matière insuffisant (commandes incluses).
- `attente_vague` : le lot est prêt mais attend les autres lots de la vague (carriers minimum).
- `temps_collecte` : temps de constitution des lots, vu du lot.
- `temps_chargement`, `temps_traitement` : le temps productif, chargement puis traitement.

**Piège :** ces colonnes se recouvrent partiellement (`temps_collecte` d'un lot recouvre `attente_pieces` / `attente_place` de son collecteur) et des lots peuvent attendre en parallèle : ne les additionnez pas pour retomber sur TO. Elles se comparent entre elles et entre postes.

### Les heures (`heures_machine`, `heures_main_oeuvre`)

Les deux colonnes de comptabilité d'atelier. Elles ne suivent **pas** la même règle d'addition, et c'est voulu :

- `heures_machine` : le temps horloge où la machine travaille réellement (chargement ou traitement), en **union** sur tous les lots : un poste est une seule machine physique, donc trois lots menés en parallèle pendant 40 minutes comptent 40 minutes machine, pas 120. Sans parallélisme, l'union égale la somme. Ce n'est **pas** le TF : le TF compte « au moins un lot engagé », y compris les attentes de ce lot (équipe pas dispo, matière manquante), tandis que les heures machine ne gardent que les instants où la machine charge ou traite. Donc les heures machine sont toujours au plus le TF, et l'écart, ce sont les attentes des lots engagés. La mise en route n'y est pas ; elle vit dans sa colonne `mise_en_route`.
- `heures_main_oeuvre` : les minutes-opérateur réservées au poste par **toutes** ses équipes, en **somme** (opérateurs x durée). Trois personnes qui travaillent une heure font trois heures de main-d'œuvre. Le compte couvre l'équipe de chargement et l'équipe de traitement par lot pendant leurs prises réelles, l'équipe de démarrage pendant le préchauffage, et une équipe par tâche sur **toute** sa fenêtre de réquisition (de la demande à la libération, temps d'attente entre lots compris, car c'est du personnel immobilisé au poste). Le rapport `heures_main_oeuvre / heures_machine` donne l'encadrement moyen du poste (personnes présentes par heure de machine qui tourne).

---

## postes_modeles.csv, la production par modèle

Pour chaque poste à pièces : le tc idéal du modèle, les pièces produites, bonnes et rebutées de ce modèle. C'est le détail qui alimente TN.

---

## buffers.csv, un buffer par ligne

- `longueur_moyenne`, `longueur_max`, `longueur_ecart_type` : la file d'attente, pondérée par le temps (une pointe d'une minute pèse une minute). **Un buffer qui gonfle désigne le goulot juste en aval.**
- `longueur_finale` : ce qui restait à la fin.
- `sejour_moyen`, `sejour_max` : temps passé par les pièces dans ce buffer (vide pour les buffers de sortie et de rebut : on n'en repart jamais).
- `entrees`, `sorties` : trafic total. Une pièce prise instantanément par un poste compte quand même.
- `flux_entrant_j`, `flux_sortant_j` : le même trafic en pièces par jour calendaire. Entrant durablement supérieur au sortant = le buffer gonfle = goulot en aval.
- `temps_moyen_entre_arrivees` : durée simulée / entrées.

---

## operateurs.csv, un groupe d'opérateurs par ligne

- `effectif` : la taille du groupe. `temps_poste` : son temps posté total (la somme de ses shifts sur la durée simulée).
- `occupation_moyenne` : le nombre moyen d'opérateurs réquisitionnés, moyenné sur toute la durée simulée (shifts et hors shifts confondus).
- `heures_en_poste` / `heures_hors_poste` : les minutes-opérateur réquisitionnées pendant / en dehors des shifts du groupe (2 opérateurs pris 90 minutes = 180). Colonnes de diagnostic : la simulation libère une équipe par tâche à la fin de son shift (même si le poste attend des pièces) et sur un abandon de lot, et re-vérifie l'ajustement au shift après l'attente de matière, donc `heures_hors_poste` doit rester proche de zéro. Avec des opérateurs contraints par leur shift, ce qui reste n'est jamais du travail : c'est une commande de réapprovisionnement (lancée en fin de shift, elle retient l'équipe jusqu'à son terme) ou, sans contrainte de shift, un lot qui se termine légitimement après.
- `taux_occupation` : le total réquisitionné (`occupation_moyenne` x durée simulée) / (`effectif` x `temps_poste`) : la part du temps posté réellement passée réquisitionné. Comme les équipes sont relâchées en fin de shift, il reste naturellement sous 100 % (au débordement d'un lot près).
- `occupation_max` : le pic d'opérateurs réquisitionnés simultanément.

---

## ressources.csv, une matière par ligne

- `capacite` : la capacité de la ressource.
- `stock_moyen`, `stock_min`, `stock_max`, `stock_final` : le niveau de stock dans le temps (moyenne pondérée par le temps), son plancher et son plafond, et ce qui restait à la fin.
- `consommation_totale`, `entrees_totales` : total consommé et total réapprovisionné sur l'exécution.
- `consommation_j` : consommation par jour calendaire.
- `nb_ruptures`, `temps_rupture` : combien de fois le stock a touché zéro, et le temps total passé à zéro. Une matière qui tombe sans cesse en rupture affame les postes qui en ont besoin ; c'est ici que vous le voyez.

---

## flux.csv et flux_modeles.csv, la ligne entière

- `sorties`, `rebuts`, `taux_rebut` : le verdict global. Grâce au générateur conscient du rebut, une pièce rebutée est refabriquée : les objectifs parlent en bonnes pièces.
- `debit_sorties_j` : bonnes pièces par jour, sur toute la durée simulée.
- `traversee_*` : le temps de traversée (lead time) des pièces sorties, de la création de la pièce à son arrivée en sortie. Moyenne, médiane, p90, max, et les mêmes colonnes **par modèle** dans `flux_modeles.csv`.
- `encours_moyen`, `encours_max`, `encours_final` : l'encours (WIP), pièces nées mais pas encore sorties ni rebutées, où qu'elles soient (buffers **et** machines). C'est pour ça qu'`encours_final` peut dépasser la somme des buffers.
- `flux_modeles.csv`, par modèle : `objectif` du générateur, `genere` (pièces effectivement injectées, refabrications comprises), sorties, rebuts, `atteinte` = sorties / objectif, et les temps de traversée (moyenne / médiane / p90 / max) du modèle. `objectif` et `atteinte` ne sont renseignés qu'en mode objectifs (critère « pièces produites ») ; en mode débit (critère « temps »), le générateur n'a pas d'objectif par modèle et ces deux colonnes restent vides, seul `genere` compte les injections.

---

## synthese_admin.csv, administratif contre productif

Un tableau qui répond à « quelle part du process part dans les postes administratifs ? ». Une ligne par indicateur, des colonnes pour les deux groupes (postes admin = oui contre = non), leurs parts et leur rapport :

- `administratives`, `productives`, `total` : la valeur cumulée de l'indicateur pour chaque groupe, et l'ensemble.
- `part_admin`, `part_productif` : la part de chaque groupe dans le total (les deux somment à 100 %). C'est le pourcentage recherché : « les postes administratifs représentent X % du temps de fonctionnement ».
- `ratio_admin_productif` : le rapport administratif / productif du même indicateur (0,25 = les postes admin en pèsent un quart de ce que pèsent les productifs).

Les cinq indicateurs (lignes) : nombre de postes, temps de fonctionnement, temps de cycle total (somme sur tous les lots), heures machine, heures main-d'œuvre.

Lecture typique : des postes d'attente/stockage marqués admin peuvent peser la majorité du temps de fonctionnement (les pièces y séjournent longtemps) tout en ne consommant presque pas d'heures main-d'œuvre (personne ne les surveille). Le tableau chiffre exactement ce déséquilibre.

---

## temps_traversee.csv, une ligne par pièce

Le détail brut : pièce, modèle, résultat (`sortie` ou `rebut`), date de création et date de fin (en dates réelles du calendrier), temps de traversée. C'est le fichier à pivoter dans Excel pour des histogrammes par modèle ou par période.

---

## graphes/, les courbes et histogrammes

Chaque figure existe en deux exemplaires : le PNG, et le CSV des données tracées (mêmes valeurs, pour refaire le graphe à votre façon). L'arborescence sépare d'abord par format, puis par type :

```
graphes/
    png/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
    csv/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
```

- `ressources/stock_*` : le stock de chaque ressource dans le temps.
- `buffers/longueur_*` : la longueur de chaque buffer dans le temps.
- `ligne/pieces_en_attente` : la somme des longueurs des buffers de passage. `ligne/encours` : les pièces nées mais ni sorties ni rebutées.
- `postes/occupation_*` : le nombre de places occupées (places prises = capacité moins places vacantes) dans le temps ; la capacité maximale du poste est rappelée dans le titre. Attention : avec des lots à empreinte fixe (contigus non), les places réservées par un lot entamé comptent.
- `operateurs/disponibles_*` : les opérateurs libres de chaque équipe dans le temps (effectif maximal dans le titre, 0 hors horaire par construction).
- `modeles/trajectoires_<modele>` : le parcours du modèle, une barre par trajectoire distincte observée (les pièces d'un même modèle peuvent suivre des chemins différents : reprises, prisons), triées de la plus fréquente à la plus rare, avec `n` et sa part. Chaque barre empile les étapes dans l'ordre ; la longueur d'un segment est le temps **moyen** passé à cette étape (bleu = attente en buffer, orange = poste). On voit d'un coup d'œil où le modèle perd son temps, branche par branche. Seules les pièces arrivées au bout (sortie ou rebut) sont comptées ; le détail exact est dans le CSV.
- `modeles/production`, par modèle : en mode objectifs, trois barres (objectif du générateur, pièces générées refabrications comprises, pièces produites en sortie) ; en mode débit, deux barres seulement (générées et produites), le générateur n'ayant pas d'objectif.

Sur les grandes exécutions, les séries temporelles très longues sont amincies au moment où les données du graphe sont écrites, en gardant la forme (les pointes et les creux sont préservés) tout en laissant tomber certains points intermédiaires exacts. Ça n'affecte que les données tracées `graph_data`, jamais les nombres des rapports CSV.

---

## run.csv, la carte d'identité de l'exécution

Le fichier source, les dates de début et de **fin** du calendrier simulé, la durée simulée, la graine aléatoire, la date de génération, le **temps de calcul** (le temps machine réel qu'a pris l'exécution), et le **critère d'arrêt** choisi avec ses paramètres (`critere_arret` = ByTime ou ByPiecesProduced, `critere_details` = ses réglages). Deux exécutions avec la même graine et le même fichier donnent exactement les mêmes CSV.

---

## Un ordre de lecture qui marche

Quand vous ouvrez un dossier de résultats et que quelque chose cloche, cet ordre vous mène d'habitude le plus vite à la cause :

1. **run.csv :** l'exécution a-t-elle seulement fini comme prévu (objectif atteint, ou arrêt sur le temps ou sur le timeout) ?
2. **flux.csv :** les totaux d'ensemble, pièces sorties, taux de rebut, encours.
3. **buffers.csv :** trouvez le buffer qui a gonflé. Le goulot est le poste juste après.
4. **postes.csv pour ce poste :** lisez ses attentes. `attente_operateurs` pointe vers le personnel, `attente_pieces` vers l'amont, `attente_matiere` vers une ressource, `attente_place` vers sa propre capacité.
5. **operateurs.csv ou ressources.csv :** confirmez la pénurie que les attentes désignaient.

Presque toute question sur une exécution se résout le long de cette chaîne.
