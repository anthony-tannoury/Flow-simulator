# Les indicateurs de la simulation, expliqués

Chaque exécution de la simulation produit un dossier `runs/<date>_<fichier>/`
contenant des CSV (UTF-8, lisibles directement dans Excel). Tout est mesuré,
pour tous les postes et tous les buffers, à chaque exécution : il n'y a rien à
activer. Ce document explique ce que chaque nombre veut dire, comment il est
mesuré, et les pièges de lecture.

## Les formats

* **Durées** : `1h 10m`, `3j 5h 20m` (et `3m 20s` sous l'heure). En interne
  tout est en minutes de simulation ; seule la présentation change.
* **Taux** : en pourcentage (`8.1%`, `83%`).
* **Instants** (création et fin d'une pièce) : en date réelle du calendrier
  (`05-01-2026 14:05`), calculée depuis la date de début de la simulation.
* **Flux** : en pièces par jour (colonnes suffixées `_j`).

## La cascade des temps, la clé de lecture

Presque tous les indicateurs par poste découlent d'un même découpage du temps,
celui de la norme NF E60-182. On part du calendrier et on retire des pertes,
étage par étage :

```
temps total (TT)            toute la durée simulée
└─ temps d'ouverture (TO)   les horaires du poste (ses shifts)
   └─ temps requis (TR)     TO moins les arrêts programmés (maintenances)
      └─ temps de fonctionnement (TF)   il y a au moins un lot sur la machine
         └─ temps de valeur ajoutée      chargement + traitement réels
            └─ temps net (TN)            reconstruit : tc idéal × pièces produites
```

Deux choses importantes :

**TN n'est pas un temps d'horloge.** C'est un temps *reconstruit* : « produire
ce que le poste a produit, à la cadence nominale, aurait dû prendre TN
minutes ». On le compare au temps de valeur ajoutée réel (chargement +
traitement) pour obtenir la performance ; l'écart, ce sont les pertes de
cadence (cycles lents, lots incomplets).

**Le tc idéal (temps de cycle idéal), pas à pas.** C'est le temps qu'il
*faudrait* au poste pour produire **une** pièce si tout se passait
parfaitement : lot plein, cadence nominale, zéro attente. Exemple concret,
un four de cuisson :

* durée de cuisson configurée : en moyenne 120 min par fournée ;
* chargement : 10 min ;
* taille maximale d'une fournée : 4 pièces.

Une fournée pleine coûte 120 + 10 = 130 minutes et sort 4 pièces, donc
`tc idéal = 130 ÷ 4 = 32,5 min par pièce`. La cadence nominale est son
inverse : 60 ÷ 32,5 ≈ 1,85 pièce/heure.

Pourquoi « la moyenne » ? Parce que les durées configurées sont des lois
aléatoires (Normal, Uniform…) : pour définir une référence stable on prend
leur moyenne (évaluée à t = 0 si les paramètres varient dans le temps). C'est
une **convention de référence**, pas une mesure — exactement comme la cadence
nominale affichée sur la fiche d'une machine réelle.

À quoi il sert : uniquement à construire TN (`tc × produites`), donc la
performance et le TRS. Si le four tourne en fournées de 2 au lieu de 4, le
temps machine est identique mais TN est divisé par deux : la performance
chute, et le TRS montre la perte. Chaque modèle a sa
durée et sa taille de lot, donc **son** tc idéal : c'est la colonne
`tc_ideal` de `postes_modeles.csv`.

## postes.csv — un poste par ligne

### Les temps (colonnes `temps_*`, `arrets_programmes`, `pannes`, `gel`, `mise_en_route`)

* `temps_total` — la durée simulée totale.
* `temps_ouverture` — le temps passé dans les horaires du poste. Mesuré sur
  l'état interne « en horaire » du poste, donc exact même avec des shifts qui
  passent minuit ou des jours fériés.
* `arrets_programmes` — les arrêts planifiés réellement pris. Un nettoyage
  « flexible » qui a glissé pour laisser finir un lot est compté là où il a
  réellement eu lieu.
* `temps_requis` — ouverture moins arrêts programmés. Le dénominateur du TRS.
* `pannes`, `nb_pannes`, `mtbf`, `mttr` — temps total en panne, nombre de
  pannes, temps moyen entre deux débuts de panne, durée moyenne d'une
  réparation. **Piège :** les pannes sont mesurées sur tout l'horizon ; une
  panne peut chevaucher une période hors horaire, donc `pannes` peut dépasser
  ce que la cascade laisse imaginer. Le MTBF n'est affiché qu'à partir de deux
  pannes observées.
* `gel` — temps passé « figé » **pendant les heures d'ouverture** : le poste a
  fini ou évacué ses lots et attend (un arrêt imminent, une condition de
  redémarrage). Un poste qui se fige juste avant la fin d'un shift n'est
  décongelé qu'au shift suivant ; la nuit qui suit n'est pas du gel, c'est de
  la fermeture, et elle n'est donc pas comptée ici.
* `mise_en_route`, `nb_mises_en_route` — temps total et nombre de démarrages
  (chauffe, réglages) ; le poste redémarre après chaque interruption (panne,
  arrêt, fin de shift). C'est le temps de réglage lui-même (la durée
  configurée), **pas** l'attente de l'équipe de démarrage : cette attente est
  une perte de disponibilité, comptée dans le TF plus bas, pas ici.
* `temps_fonctionnement` — le temps avec au moins un lot actif sur le poste.
  C'est le TF de la cascade.

### Les taux (colonnes `taux_de_charge` → `tre`)

* `taux_de_charge` = TR / TO. Quelle part de l'ouverture est réellement
  engagée (le reste part en arrêts programmés).
* `disponibilite` (Do) = TF / TR. Quand le poste devait tourner, a-t-il
  tourné ? Les pertes ici : pannes, démarrages, attente de l'équipe de
  démarrage, gel, et surtout la **famine** (pas de pièces à traiter). Un poste
  goulot très affamé aura un Do faible : c'est réel, pas une anomalie.
* `performance` (Tp) = TN ÷ (temps de chargement + temps de traitement), soit
  le temps de valeur ajoutée idéal rapporté au temps machine réellement passé
  à charger et traiter (additionné sur **tous** les lots). Quand il tournait,
  tournait-il à la cadence nominale ? Les pertes : cycles plus lents que la
  moyenne, productivité des équipes, et surtout **lots incomplets** (un gabarit
  qui tourne avec 2 pièces sur 4 possibles).
  Pourquoi additionner sur tous les lots plutôt que diviser par TF ? Parce
  qu'un poste peut traiter plusieurs lots **en parallèle** (`carriers
  indépendants`, zones d'attente/stockage). Le TF « au moins un lot actif »
  sous-compterait ce travail parallèle et ferait dépasser 100 %. Additionner le
  temps de chaque lot corrige ça : Tp reste dans [0, 100 %].
* `qualite` (Tq) = bonnes / produites. Les « bonnes » pièces d'un poste sont
  celles que son trieur aval n'a pas envoyées au rebut ; un poste sans routage
  vers le rebut a Tq = 1.
* `trs` = Do × Tp × Tq. L'indicateur roi (c'est la définition standard du TRS :
  disponibilité × performance × qualité), toujours dans [0, 100 %].
* `trg` = TRS × taux_de_charge : comme le TRS mais les arrêts programmés
  comptent en perte.
* `tre` = TRS × (TR ÷ TT) : tout le calendrier compte, même les nuits fermées.

### La production (colonnes `pieces_*`, `nb_lancements`, `taille_lot_moyenne`, `cycle_*`, `debit_pieces_j`, `flux_*`)

* `pieces_produites` — pièces déposées en sortie par les lots menés à terme
  (les lots évacués par une panne ou un arrêt ne comptent pas : ils n'ont rien
  produit). Pour un poste de transformation de matière, c'est la quantité
  d'unités transformées.
* `pieces_bonnes`, `pieces_rebutees` — répartition de ces pièces selon le
  verdict du trieur aval immédiat.
* `nb_lancements` — nombre de lots menés à terme ; `taille_lot_moyenne` — leur
  taille moyenne. Une taille moyenne loin du maximum = pertes de performance.
* `cycle_moyen`, `cycle_p90`, `cycle_max` — durée d'un lot, de sa création
  (début de collecte) au dépôt des pièces. Le p90 dit : « 9 lots sur 10
  finissent en moins de X minutes ».
* `debit_pieces_j` — pièces produites par **jour de temps requis** (la
  cadence réelle du poste quand il est censé tourner).
* `flux_entrant_j` — pièces physiquement prises dans les buffers d'entrée,
  par jour calendaire (les reprises après évacuation comptent : c'est le flux
  physique). `flux_sortant_j` — pièces déposées en sortie, par jour
  calendaire. Un poste qui entre plus qu'il ne sort accumule ou évacue.

### Les attentes (colonnes `attente_*`, `temps_collecte`, `temps_chargement`, `temps_traitement`)

C'est le diagnostic du goulot : où part le temps qui manque à la
disponibilité ? Chaque lot étiquette ce qu'il est en train de faire, et on
additionne :

* `attente_pieces` — famine en amont : le collecteur attend des pièces.
* `attente_place` — le poste est plein : plus de place libre (max_capacity).
* `attente_operateurs` — l'équipe demandée n'est pas disponible.
* `attente_matiere` — stock de matière insuffisant (commandes incluses).
* `attente_vague` — le lot est prêt mais attend les autres lots de la vague
  (min_carriers).
* `temps_collecte` — temps de constitution des lots, vu du lot.
* `temps_chargement`, `temps_traitement` — le temps « productif », chargement
  puis traitement.

**Piège :** ces colonnes se recouvrent partiellement (`temps_collecte` d'un
lot recouvre `attente_pieces`/`attente_place` de son collecteur) et des lots
peuvent attendre en parallèle : ne les additionnez pas pour retomber sur TO.
Elles se comparent entre elles et entre postes.

## postes_modeles.csv — la production par modèle

Pour chaque poste à pièces : le tc idéal du modèle, les pièces produites,
bonnes et rebutées de ce modèle. C'est le détail qui alimente TN.

## buffers.csv — un buffer par ligne

* `longueur_moyenne`, `longueur_max`, `longueur_ecart_type` — la file
  d'attente, pondérée par le temps (une pointe d'une minute pèse une minute).
  **Un buffer qui gonfle désigne le goulot juste en aval.**
* `longueur_finale` — ce qui restait à la fin.
* `sejour_moyen`, `sejour_max` — temps passé par les pièces dans ce buffer
  (vide pour les buffers de sortie et de rebut : on n'en repart jamais).
* `entrees`, `sorties` — trafic total. Une pièce prise instantanément par un
  poste compte quand même.
* `flux_entrant_j`, `flux_sortant_j` — le même trafic en pièces par jour
  calendaire. Entrant durablement supérieur au sortant = le buffer gonfle =
  goulot en aval.
* `temps_moyen_entre_arrivees` — durée simulée ÷ entrées.

## flux.csv et flux_modeles.csv — la ligne entière

* `sorties`, `rebuts`, `taux_rebut` — le verdict global. Grâce au générateur
  « conscient du rebut », une pièce rebutée est refabriquée : les objectifs
  parlent en bonnes pièces.
* `debit_sorties_j` — bonnes pièces par jour, sur toute la durée simulée.
* `traversee_*` — le temps de traversée (lead time) des pièces sorties : de la
  création de la pièce à son arrivée en sortie. Moyenne, médiane, p90, max —
  et les mêmes colonnes **par modèle** dans `flux_modeles.csv`.
* `encours_moyen`, `encours_max`, `encours_final` — l'encours (WIP) : pièces
  nées mais pas encore sorties ni rebutées, où qu'elles soient (buffers **et**
  machines). C'est pour ça qu'`encours_final` peut dépasser la somme des
  buffers.
* `flux_modeles.csv` — par modèle : objectif du générateur, sorties, rebuts,
  `atteinte` = sorties ÷ objectif, et les temps de traversée
  (moyenne / médiane / p90 / max) du modèle.

## temps_traversee.csv — une ligne par pièce

Le détail brut : pièce, modèle, résultat (`sortie` ou `rebut`), date de
création et date de fin (en dates réelles du calendrier), temps de traversée.
C'est le fichier à pivoter dans Excel pour des histogrammes par modèle ou par
période.

## graphes/ — les courbes et histogrammes

Chaque figure existe en deux exemplaires : le PNG, et le CSV des données
tracées (mêmes valeurs, pour refaire le graphe à votre façon). L'arborescence
sépare d'abord par format, puis par type :

```
graphes/
    png/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
    csv/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
```

* `ressources/stock_*` — le stock de chaque ressource dans le temps.
* `buffers/longueur_*` — la longueur de chaque buffer dans le temps.
* `ligne/pieces_en_attente` — la somme des longueurs des buffers de passage ;
  `ligne/encours` — les pièces nées mais ni sorties ni rebutées.
* `postes/occupation_*` — le nombre de places occupées (places prises =
  capacité − places vacantes) dans le temps ; la capacité maximale du poste
  est rappelée dans le titre. Attention : avec des lots à empreinte fixe
  (`contiguous = non`), les places réservées par un lot entamé comptent.
* `operateurs/disponibles_*` — les opérateurs libres de chaque équipe dans le
  temps (effectif maximal dans le titre, 0 hors horaire par construction).
* `modeles/trajectoires_<modele>` — le parcours du modèle : une barre par
  trajectoire distincte observée (les pièces d'un même modèle peuvent suivre
  des chemins différents : reprises, prisons…), triées de la plus fréquente à
  la plus rare, avec `n` et sa part. Chaque barre empile les étapes dans
  l'ordre ; la longueur d'un segment est le temps **moyen** passé à cette
  étape (bleu = attente en buffer, orange = poste). On voit d'un coup d'œil
  où le modèle perd son temps, branche par branche. Seules les pièces
  arrivées au bout (sortie ou rebut) sont comptées ; le détail exact est dans
  le CSV.
* `modeles/production` — par modèle, trois barres : objectif du générateur,
  pièces générées (refabrications comprises), pièces produites (sorties).

## run.csv — la carte d'identité de l'exécution

Fichier source, dates de début et de **fin** du calendrier simulé, durée
simulée, graine aléatoire, date de génération, **temps de calcul** (le temps
machine réel qu'a pris l'exécution), et le **critère d'arrêt** choisi avec ses
paramètres (`critere_arret` = ByTime ou ByPiecesProduced, `critere_details` =
ses réglages). Deux exécutions avec la même graine et le même fichier donnent
exactement les mêmes CSV.
