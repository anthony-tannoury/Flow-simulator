# Les indicateurs de la simulation, expliqués

Chaque exécution de la simulation produit un dossier `runs/<date>_<fichier>/`
contenant des CSV (UTF-8, lisibles directement dans Excel). Tout est mesuré,
pour tous les postes et tous les buffers, à chaque exécution : il n'y a rien à
activer. Ce document explique ce que chaque nombre veut dire, comment il est
mesuré, et les pièges de lecture.

Toutes les durées sont en **minutes de simulation**.

## La cascade des temps, la clé de lecture

Presque tous les indicateurs par poste découlent d'un même découpage du temps,
celui de la norme NF E60-182. On part du calendrier et on retire des pertes,
étage par étage :

```
temps total (TT)            toute la durée simulée
└─ temps d'ouverture (TO)   les horaires du poste (ses shifts)
   └─ temps requis (TR)     TO moins les arrêts programmés (maintenances)
      └─ temps de fonctionnement (TF)   il y a au moins un lot sur la machine
         └─ temps net (TN)              reconstruit : tc idéal × pièces produites
            └─ temps utile (TU)         reconstruit : tc idéal × bonnes pièces
```

Deux choses importantes :

**TN et TU ne sont pas des temps d'horloge.** Ce sont des temps *reconstruits* :
« produire ce que le poste a produit, à la cadence nominale, aurait dû prendre
TN minutes ». La différence entre TF et TN, ce sont les pertes de cadence ;
entre TN et TU, les pertes de qualité.

**Le tc idéal (temps de cycle idéal) est déclaré, pas mesuré.** C'est la
cadence nominale du poste. Il est calculé depuis la configuration du poste :
moyenne de la durée de traitement plus moyenne du chargement, réparties sur un
lot plein. Par modèle : `tc = (durée_moyenne + chargement_moyen) / taille_max_du_lot`.
C'est vrai de tout TRS en usine : la cadence nominale est une convention.

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
* `gel` — temps passé « figé » : le poste a fini ou évacué ses lots et attend
  (un arrêt imminent, une reprise d'horaire, une condition de redémarrage).
* `mise_en_route`, `nb_mises_en_route` — temps total et nombre de démarrages
  (chauffe, réglages) ; le poste redémarre après chaque interruption. Le temps
  inclut l'attente de l'équipe de démarrage.
* `temps_fonctionnement` — le temps avec au moins un lot actif sur le poste.
  C'est le TF de la cascade.

### Les taux (colonnes `taux_de_charge` → `tre`)

* `taux_de_charge` = TR / TO. Quelle part de l'ouverture est réellement
  engagée (le reste part en arrêts programmés).
* `disponibilite` (Do) = TF / TR. Quand le poste devait tourner, a-t-il
  tourné ? Les pertes ici : pannes, démarrages, et toutes les attentes.
* `performance` (Tp) = TN / TF. Quand il tournait, tournait-il à la cadence
  nominale ? Les pertes ici : cycles plus lents que la moyenne, productivité
  des équipes, et surtout **lots incomplets** — un gabarit qui tourne avec 2
  pièces sur 4 possibles passe le même temps machine pour moitié moins de
  production.
* `qualite` (Tq) = bonnes / produites. Les « bonnes » pièces d'un poste sont
  celles que son trieur aval n'a pas envoyées au rebut ; un poste sans routage
  vers le rebut a Tq = 1.
* `trs` = TU / TR = Do × Tp × Tq. L'indicateur roi. Équivalent à
  `bonnes ÷ (TR × cadence nominale)` : ce qu'on a produit de bon, rapporté à
  ce que le temps requis aurait permis à cadence nominale.
* `trg` = TU / TO : comme le TRS mais les arrêts programmés comptent en perte
  (`trg = trs × taux_de_charge`).
* `tre` = TU / TT : tout le calendrier compte, même les nuits fermées.

### La production (colonnes `pieces_*`, `nb_lancements`, `taille_lot_moyenne`, `cycle_*`, `debit_pieces_h`)

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
* `debit_pieces_h` — pièces produites par heure de temps requis.

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
bonnes et rebutées de ce modèle. C'est le détail qui alimente TN et TU.

## buffers.csv — un buffer par ligne

* `longueur_moyenne`, `longueur_max`, `longueur_ecart_type` — la file
  d'attente, pondérée par le temps (une pointe d'une minute pèse une minute).
  **Un buffer qui gonfle désigne le goulot juste en aval.**
* `longueur_finale` — ce qui restait à la fin.
* `sejour_moyen`, `sejour_max` — temps passé par les pièces dans ce buffer
  (vide pour les buffers de sortie et de rebut : on n'en repart jamais).
* `entrees`, `sorties` — trafic total. Une pièce prise instantanément par un
  poste compte quand même.
* `temps_moyen_entre_arrivees` — durée simulée ÷ entrées.

## flux.csv et flux_modeles.csv — la ligne entière

* `sorties`, `rebuts`, `taux_rebut` — le verdict global. Grâce au générateur
  « conscient du rebut », une pièce rebutée est refabriquée : les objectifs
  parlent en bonnes pièces.
* `debit_sorties_h` — bonnes pièces par heure, sur toute la durée simulée.
* `traversee_*` — le temps de traversée (lead time) des pièces sorties : de la
  création de la pièce à son arrivée en sortie. Moyenne, médiane, p90, max.
* `encours_moyen`, `encours_max`, `encours_final` — l'encours (WIP) : pièces
  nées mais pas encore sorties ni rebutées, où qu'elles soient (buffers **et**
  machines). C'est pour ça qu'`encours_final` peut dépasser la somme des
  buffers.
* `flux_modeles.csv` — par modèle : objectif du générateur, sorties, rebuts,
  `atteinte` = sorties ÷ objectif.

## temps_traversee.csv — une ligne par pièce

Le détail brut : pièce, modèle, résultat (`sortie` ou `rebut`), date de
création, date de fin, temps de traversée. C'est le fichier à pivoter dans
Excel pour des histogrammes par modèle ou par période.

## series_temporelles.csv — les courbes

Format long (`serie`, `nom`, `t`, `valeur`) :

* `longueur_buffer` — la longueur de chaque buffer à chaque changement ;
* `encours` — la courbe d'encours de la ligne.

Chaque ligne est un changement de valeur (données en escalier) : pour tracer,
utilisez un graphique en escalier, ou étirez chaque valeur jusqu'au `t`
suivant.

## run.csv — la carte d'identité de l'exécution

Fichier source, date de début du calendrier, graine aléatoire, durée simulée,
date de génération. Deux exécutions avec la même graine et le même fichier
donnent exactement les mêmes CSV.
