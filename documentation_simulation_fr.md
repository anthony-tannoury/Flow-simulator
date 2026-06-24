# Comprendre la simulation (`simulation.py`)

> **À qui s'adresse ce document ?**
> À toute personne qui **conçoit un flux** dans l'éditeur (`flow_designer.py`) et
> veut comprendre *ce que la simulation fait réellement* avec les cartes qu'elle
> dépose et qu'elle relie. On ne décrit pas le code : on décrit le **fonctionnement**
> du modèle, le vocabulaire, et surtout **les règles** que votre flux doit respecter
> pour être valide.

---

## 1. L'idée générale

`simulation.py` est un **simulateur à événements discrets** d'un atelier de
production. Concrètement :

- des **pièces** sont créées par une source,
- elles patientent dans des **stocks** (les buffers),
- des **postes de travail** (les tâches) les prennent, les transforment pendant
  une certaine durée, puis les reposent dans d'autres stocks,
- et ainsi de suite jusqu'à la sortie.

Le temps n'avance pas en continu : il « saute » d'un événement au suivant (une
pièce créée, une tâche terminée, une panne qui démarre…). C'est ce qui permet de
simuler des heures ou des jours de production en quelques secondes.

Le flux que vous dessinez est donc un **réseau** :

```
   Source ──► Stock ──► Poste ──► Stock ──► Poste ──► … ──► Stock de sortie
 (FirstTask)  (Buffer)  (Task)   (Buffer)  (Task)            (Buffer)
```

Tout le reste (lois de durée, opérateurs, consommables, pannes, arrêts, mesures)
vient **se brancher** sur ces éléments pour les paramétrer.

---

## 2. Les modèles de pièces (`Model`)

Chaque pièce appartient à **un modèle**. Les modèles forment une **hiérarchie**
parent / enfant, exactement comme une famille de produits :

```
        M1                 M2
       /  \                |
     C1    C2             C3
```

Ici `C1` et `C2` sont des variantes de `M1`, et `C3` une variante de `M2`.

**Règle clé à retenir — l'héritage descendant :**
> Quand un stock ou une tâche « accepte » un modèle, il accepte **ce modèle et
> tous ses descendants**.

Donc un stock qui accepte `M1` accepte aussi `C1` et `C2`. Mais un stock qui
accepte seulement `C1` n'accepte **pas** `M1` ni `C2`. Pensez aux modèles parents
comme à des **catégories** : choisir une catégorie large, c'est accepter toute sa
descendance ; choisir une feuille précise, c'est ne prendre que celle-là.

C'est le mécanisme central qui fait que les pièces sont **triées** automatiquement
dans le bon stock et prises par le bon poste.

---

## 3. Les pièces (`Piece`)

Une pièce, c'est simplement **un modèle + un identifiant unique**. Elle porte aussi
l'instant de sa création (utilisé pour mesurer son temps de traversée). Les pièces
ne « décident » de rien : elles se laissent transporter par le flux. Ce sont les
stocks et les tâches qui décident **qui prend quoi**.

---

## 4. Les stocks (buffers)

Il existe **deux natures de stock** très différentes. Bien les distinguer est
essentiel au moment de la conception.

### 4.1 Le stock réel — `HardBuffer`

C'est une **file d'attente concrète** : les pièces y patientent réellement.

- Il a une **liste de modèles acceptés** (avec l'héritage du §2).
- Une tâche vient y **piocher** les pièces dont elle a besoin.
- C'est le seul endroit où les pièces « existent » entre deux postes.
- C'est aussi le seul type de stock que l'on peut **observer** avec un *Monitor*
  (voir §10).

> En pratique, dans la visualisation animée (`visual_simulation.py`), ce sont les
> `HardBuffer` qui affichent les petits carrés colorés (une pièce = un carré,
> couleur = modèle racine) et un compteur.

### 4.2 L'aiguillage probabiliste — `SoftBuffer`

Un `SoftBuffer` **n'est pas un vrai stock** : c'est un **routeur**. Il ne garde
aucune pièce ; il décide **vers quel stock réel** envoyer chaque pièce, selon des
**probabilités**.

Exemple : « 70 % des pièces partent vers le stock A, 30 % vers le stock B ».

**Règles à respecter pour un `SoftBuffer` :**
1. **Toutes** les destinations doivent accepter **exactement les mêmes modèles**.
   (On ne peut pas tirer au sort entre des stocks qui n'attendent pas les mêmes
   pièces.)
2. Chaque probabilité est comprise entre **0 et 1**.
3. La **somme** des probabilités doit faire **1**.

Utilisez un `SoftBuffer` pour modéliser un **partage de flux aléatoire** (par
exemple un contrôle qualité qui envoie un certain pourcentage en retouche).

---

## 5. La source — `FirstTask`

C'est le **point d'entrée** des pièces dans l'atelier. En boucle, elle :

1. tire une durée (loi de probabilité) — l'intervalle entre deux créations,
2. choisit un **modèle** selon des probabilités que vous fixez,
3. attend la durée,
4. dépose la nouvelle pièce dans ses stocks de sortie.

Elle peut aussi **consommer des ressources** à chaque création (matière première,
par exemple).

**Règles :**
- Les probabilités des modèles sont dans **[0, 1]** et **somment à 1**.
- Les **stocks de sortie doivent former une partition** des modèles générés
  (voir l'encadré « partition » au §6.3) : chaque pièce produite doit avoir
  **une et une seule** destination possible.

---

## 6. Les postes de travail — `Task`

C'est le cœur du modèle. Une tâche **prend des pièces dans ses stocks d'entrée**,
les **traite** pendant une durée, puis les **dépose dans ses stocks de sortie**.

### 6.1 Ce qu'une tâche sait faire — la *capability*

La *capability* est la **liste des modèles que la tâche sait traiter** (héritage
inclus). Une tâche ne piochera jamais une pièce qu'elle ne sait pas traiter, même
si elle est disponible dans un stock d'entrée.

### 6.2 Le traitement par lots (*batch*)

Une tâche ne traite pas forcément les pièces une par une. Elle constitue un **lot** :

- **`min_capacity`** : il faut **au moins** ce nombre de pièces pour démarrer.
- **`max_capacity`** : le lot (et le nombre de pièces simultanément « en cours »)
  ne peut pas dépasser cette taille.

Régler `min = max = 1` donne un traitement **pièce par pièce** classique.

**Deux stratégies de constitution du lot (`batch_collector`) :**

| Stratégie | Comportement |
|---|---|
| **`GreedyBatchCollector`** (gourmand) | Dès que `min_capacity` pièces sont réunies, le lot démarre. S'il reste des pièces disponibles tout de suite, il en prend autant que possible jusqu'à `max_capacity`. Réactif : on ne laisse pas traîner. |
| **`AltruisticBatchCollector`** (altruiste) | Ne saisit des pièces **que** si un lot complet d'au moins `min_capacity` peut être formé **d'un seul coup**. Tant que ce n'est pas le cas, il **ne prend rien** et laisse les pièces disponibles pour d'autres postes. Évite de « bloquer » des pièces en otage. |

Choisissez *greedy* pour la vitesse, *altruiste* quand plusieurs postes se
partagent les mêmes stocks et que vous ne voulez pas qu'un poste accapare des
pièces qu'il ne peut pas encore traiter.

### 6.3 Où vont les pièces finies — les stocks de sortie

> **La règle de la partition.**
> Les stocks de sortie d'une tâche doivent former une **partition** de sa
> *capability* : les modèles couverts par les différentes sorties ne doivent **pas
> se chevaucher** (disjoints), et **ensemble** ils doivent couvrir **tous** les
> modèles que la tâche sait produire.
>
> Autrement dit : pour chaque pièce qui sort, il existe **exactement un** stock de
> sortie capable de la recevoir — ni zéro (pièce bloquée), ni deux (ambiguïté).

Une sortie peut être un `HardBuffer` (stock réel) ou un `SoftBuffer` (aiguillage).

### 6.4 Les opérateurs et les consommables

Une tâche peut exiger des **ressources** pour fonctionner :

- **Opérateurs** (`operators`) — des ressources réutilisables (ex. : un technicien).
  La tâche les **emprunte** puis les **rend**.
- **Consommables** (`resources`) — des ressources qui se vident (ex. : matière,
  visserie), modélisées par des *RestockableResource* (voir §8).

Chaque catégorie a une **portée** (*scope*) qui dit **à quelle fréquence** la
ressource est demandée :

| Portée | Signification |
|---|---|
| **`PER_PIECE`** | par pièce du lot (la quantité est multipliée par la taille du lot). |
| **`PER_BATCH`** | une fois par lot, quelle que soit sa taille. |
| **`PER_TASK`** | une fois pour toute la durée de vie du poste (la ressource reste mobilisée). |

**Contraintes importantes :**
- Les **opérateurs** ne peuvent **pas** être en `PER_PIECE` (uniquement
  `PER_BATCH` ou `PER_TASK`).
- Les **consommables** ne peuvent **pas** être en `PER_TASK` (uniquement
  `PER_PIECE` ou `PER_BATCH`).

### 6.5 Le démarrage (*startup*)

Avant de pouvoir traiter, une tâche peut devoir **se préparer** : une
**durée de démarrage** (`startup_duration`) et éventuellement des **opérateurs de
démarrage** (`startup_operators`). C'est le réglage / la mise en route de la
machine.

Point important : après une **panne** ou un **arrêt programmé**, la tâche est
considérée comme « éteinte » et devra **redémarrer** (re-payer ce temps de
préparation).

### 6.6 Postes en pipeline — `independent_carriers`

- **`independent_carriers = False`** (par défaut) : la tâche attend qu'un lot soit
  **complètement terminé** avant d'en commencer un autre. Traitement **séquentiel**.
- **`independent_carriers = True`** : la tâche peut **enchaîner** : pendant qu'un
  lot est en cours, le suivant peut déjà être chargé. Cela modélise un poste où
  plusieurs lots avancent **en parallèle** (dans la limite de `max_capacity`).

---

## 7. Les ressources réutilisables — `Resource`

Une `Resource` est un **pool de capacité** : par exemple « 3 opérateurs ». Les
tâches en **empruntent** une partie le temps de travailler, puis la **rendent**.
Si la ressource n'est pas disponible, la tâche **attend** son tour.

C'est le bon outil pour modéliser une **contention** : plusieurs postes qui se
disputent un nombre limité de personnes ou de machines.

---

## 8. Les consommables réapprovisionnables — `RestockableResource`

C'est une ressource qui **se vide** (matière première, composants, etc.) et qui se
**réapprovisionne automatiquement** :

- Elle a une **capacité** (le stock plein) et un **seuil** (`threshold`).
- Dès que le niveau **passe sous le seuil**, une **commande** part.
- Après une **durée de livraison** (`delivery_duration`), le stock est ramené à sa
  capacité.

Vous pilotez donc le comportement « juste-à-temps » de l'atelier : un seuil bas =
des ruptures possibles ; un seuil haut = on commande tôt et souvent.

---

## 9. Les aléas : pannes et arrêts programmés

### 9.1 Les pannes — `Breakdown`

Une panne se rattache à **une tâche** et fonctionne avec deux lois :

- **MTBF** (*Mean Time Between Failures*) : le temps de bon fonctionnement avant la
  prochaine panne.
- **MTTR** (*Mean Time To Repair*) : la durée de réparation.

**Que deviennent les pièces en cours pendant la panne ?**
> Le lot en cours de traitement est **interrompu** et les pièces sont **évacuées**
> vers les **stocks de sortie de la panne** (`bufs_out` du *Breakdown*). C'est là
> que l'on modélise les **rebuts** (pièces perdues / mises au rebut), ou un
> **réacheminement** vers une zone de reprise.

Après réparation, la tâche doit **redémarrer** (voir §6.5).

> Dans la visualisation, un poste en panne s'affiche en **rouge** (`● BREAKDOWN`).

### 9.2 Les arrêts programmés — `ScheduledShutdowns` & `Interval`

Contrairement aux pannes (aléatoires), ce sont des **arrêts planifiés** : pauses,
nuits, week-ends, maintenance préventive…

- Un **`Interval`** est une fenêtre de temps `[début, fin]` (avec `début ≤ fin`).
- Un **`ScheduledShutdowns`** regroupe plusieurs intervalles, qui doivent être
  **disjoints** (ils ne se chevauchent pas).

Quand un arrêt programmé approche, la tâche **finit proprement** ce qu'elle peut,
**se met en pause** pendant l'intervalle (en libérant ses opérateurs `PER_TASK`),
puis **redémarre** à la fin.

> Dans la visualisation, un poste en arrêt programmé s'affiche en **violet**
> (`● SHUTDOWN`).

---

## 10. Les mesures — `Monitor`

Un `Monitor` s'attache à **un `HardBuffer`** (seuls les stocks réels sont
mesurables) et collecte des **statistiques** sur toute la durée de la simulation.
Vous choisissez quelles mesures activer :

| Mesure | Ce qu'elle dit |
|---|---|
| **Longueur moyenne** | nombre moyen de pièces en attente dans le stock. |
| **Longueur max** | pic de remplissage. |
| **Écart-type de longueur** | régularité du remplissage. |
| **Longueur finale** | nombre de pièces restées à la fin. |
| **Temps de séjour moyen / max** | combien de temps une pièce reste dans le stock. |
| **Temps moyen avant arrivée** | délai entre la création d'une pièce et son entrée dans ce stock (temps de traversée amont). |
| **Débit (*throughput*)** | nombre de pièces ayant transité par le stock. |

Ces chiffres sont imprimés en fin de simulation (`print_statistics()` dans
`graph_parser.py`). Ils servent à repérer les **goulots d'étranglement** (un stock
qui gonfle), les **postes sous-alimentés**, ou un **débit** insuffisant.

---

## 11. Les lois de durée — `Distribution`

Presque toutes les durées du modèle (création de pièces, traitement, démarrage,
MTBF, MTTR, livraison…) sont pilotées par une **loi de probabilité** plutôt que par
une valeur fixe, pour refléter la **variabilité** réelle :

- **`Constant`** — toujours la même valeur (pas d'aléa).
- **`Normal`** — autour d'une moyenne, avec un écart-type (loi « en cloche »).
- **`Triangular`** — entre un minimum et un maximum, avec une valeur la plus
  probable.
- **`Exponential`** — typique des temps entre événements rares (pannes, arrivées).

Au moment de la conception, c'est avec ces lois que vous réglez le **rythme** et
l'**incertitude** de votre atelier.

---

## 12. Comment une pièce circule, du début à la fin

Pour fixer les idées, voici le **cycle de vie** d'une pièce :

1. **Naissance.** La source (`FirstTask`) crée la pièce d'un certain modèle et la
   dépose dans un stock de sortie (directement, ou via un `SoftBuffer` qui
   l'aiguille).
2. **Attente.** La pièce patiente dans un `HardBuffer` parmi celles que ce stock
   accepte.
3. **Prise en charge.** Un poste (`Task`) dont la *capability* couvre le modèle de
   la pièce la pioche — éventuellement avec d'autres pour former un **lot** —
   lorsqu'il dispose des **opérateurs** et **consommables** nécessaires, et qu'il
   n'est ni en panne ni à l'arrêt.
4. **Transformation.** Le poste traite le lot pendant sa **durée de traitement**.
5. **Sortie.** Les pièces finies sont déposées dans le **stock de sortie**
   correspondant à leur modèle (règle de partition du §6.3).
6. **Répétition.** Les étapes 2 à 5 se répètent de poste en poste.
7. **Fin de parcours.** La pièce finit dans un **stock de sortie** terminal (rôle
   « Exit »), ou au **rebut** si elle a été évacuée lors d'une panne (rôle
   « Scrap »).

---

## 13. Mémo des règles à respecter (check-list de conception)

Avant de lancer une simulation, vérifiez que votre flux respecte ces contraintes.
La plupart sont **vérifiées au démarrage** : si elles ne sont pas respectées, le
modèle refuse de se construire et signale l'erreur (celle marquée *(bon sens)*
n'est pas bloquante mais reste à respecter) :

- [ ] **Modèles** : la hiérarchie parent/enfant est cohérente ; rappelez-vous que
      choisir un parent inclut tous ses enfants.
- [ ] **`SoftBuffer`** : toutes les destinations acceptent les **mêmes** modèles ;
      probabilités dans [0, 1] et de **somme 1**.
- [ ] **Sorties de `Task` / `FirstTask`** : elles forment une **partition** de la
      *capability* / des modèles produits (disjointes **et** couvrant tout).
- [ ] **`FirstTask`** : probabilités des modèles dans [0, 1] et de **somme 1**.
- [ ] **Opérateurs** : portée `PER_BATCH` ou `PER_TASK` (**jamais `PER_PIECE`**).
- [ ] **Consommables** : portée `PER_PIECE` ou `PER_BATCH` (**jamais `PER_TASK`**).
- [ ] **Lots** *(bon sens)* : `min_capacity ≤ max_capacity`.
- [ ] **`Interval`** : `début ≤ fin`.
- [ ] **`ScheduledShutdowns`** : intervalles **disjoints**.
- [ ] **`Monitor`** : branché sur un **`HardBuffer`** (les `SoftBuffer` ne se
      mesurent pas).

---

## 14. Et après ? Visualiser le flux

Une fois le flux exporté en JSON depuis l'éditeur, deux outils l'exploitent :

- **`graph_parser.py`** — reconstruit le modèle et, à la fin, **imprime les
  statistiques** des *Monitors*.
- **`visual_simulation.py`** — **anime** le flux : il dessine chaque carte à sa
  place (mêmes couleurs que l'éditeur), trace les liaisons, et montre **en direct**
  les pièces s'accumuler dans les stocks et les postes changer d'état (gris =
  inactif, clair = en cours, rouge = panne, violet = arrêt). On s'y **déplace à la
  souris** (glisser pour naviguer, molette pour zoomer) plutôt que de tout
  comprimer dans une seule fenêtre.

C'est le meilleur moyen de **vérifier visuellement** que les pièces circulent comme
prévu et de repérer d'un coup d'œil les stocks qui gonflent.
