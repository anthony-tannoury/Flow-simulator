# Guide de l'éditeur de flux (`flow_designer.py`)

> **À qui s'adresse ce document ?**
> À quelqu'un qui **débute avec l'éditeur** mais qui a **déjà lu**
> `documentation_simulation_fr.md`. On ne réexplique donc pas les concepts (modèles,
> pièces, stocks, postes, lots, ressources, pannes, arrêts, mesures) : on montre
> **comment les dessiner** dans l'éditeur, **comment les relier** et **comment
> exporter** le flux pour le simuler.
>
> Si un terme vous échappe (capability, portée PER_BATCH, partition des sorties,
> SoftBuffer…), revenez à la doc de la simulation : ici on suppose qu'il est acquis.

---

## 1. À quoi sert l'éditeur

`flow_designer.py` est un **éditeur graphique de nœuds**. Ce n'est **pas** un
simulateur : son seul rôle est de vous laisser dessiner l'atelier sous forme de
**cartes reliées par des fils**, puis d'**exporter un fichier JSON propre**
(« clean JSON »). Ce JSON est ensuite lu par :

- `graph_parser.py` — construit le modèle et imprime les statistiques, et
- `visual_simulation.py` — anime le flux.

Chaque **carte** (nœud) correspond exactement à un concept de la simulation
(`Task`, `HardBuffer`, `Distribution`…). Chaque carte a des **ports** :

- les **entrées** sont à **gauche**,
- les **sorties** sont à **droite**,
- les ports sont **colorés par type** (durée, ressource, stock, tâche…) ; on ne
  peut relier que des ports compatibles (voir §6).

---

## 2. Installation et lancement

L'éditeur a besoin de **NodeGraphQt** et d'un **binding Qt** :

```bash
pip install NodeGraphQt PySide6 Qt.py
# ou, selon l'environnement :
pip install NodeGraphQt PySide2
```

Puis :

```bash
python flow_designer.py
```

Une fenêtre « **Simulation Flow Editor** » s'ouvre, avec une grande zone de travail
(le *canvas*) et une barre de menus.

**Navigation dans le canvas :**
- molette = zoom,
- clic-droit glissé (ou glisser sur le fond) = se déplacer,
- clic sur une carte = sélection ; glisser = déplacer la carte,
- glisser d'un port à un autre = créer un fil,
- pour **supprimer** un fil : refaites un glisser depuis le port, ou supprimez une
  des cartes.

---

## 3. La barre de menus en un coup d'œil

| Menu | Entrées | Rôle |
|---|---|---|
| **File** | New · Import clean JSON… · Export clean JSON… | Nouveau flux, ouvrir/enregistrer un flux au format JSON. |
| **Models** | Edit models… | Définir la **liste globale des modèles** et leur hiérarchie. |
| **Edit** | Delete selected *(touche Suppr)* | Supprimer les cartes sélectionnées. |
| **Tools** | Validate graph · Auto-layout selected · Frame all | Vérifier le flux, ranger automatiquement, recadrer la vue. |
| **Templates** | Add Task Template · Add FirstTask Template · Add Backdrop Around Selection | Déposer un **poste déjà câblé** (avec ses durées, ressources…) ou encadrer une sélection. |
| **Create** | Distribution · Interval · Scheduled Shutdowns · Resource · Restockable Resource · Hard Buffer · Soft Buffer · First Task · Task · Breakdown · Monitor | Déposer une carte **vierge** de chaque type. |

> 💡 **Conseil débutant :** pour un premier poste, utilisez **Templates → Add Task
> Template**. Il dépose un `Task` déjà relié à ses distributions, une ressource, un
> opérateur, etc. Vous n'avez plus qu'à ajuster. Idem avec **Add FirstTask
> Template** pour la source.

---

## 4. Le bon ordre des opérations

Un déroulé fiable du début à la fin :

1. **Définir les modèles** — `Models → Edit models…` (voir §5). À faire **en
   premier** : tout le reste (capabilities, stocks, source) s'appuie dessus.
2. **Déposer les cartes** — via **Create** (cartes vierges) ou **Templates**
   (postes pré-câblés).
3. **Configurer chaque carte** — **double-clic** ouvre la boîte de dialogue adaptée
   (voir §7). Certains champs simples (start/end d'un `Interval`, capacité d'une
   `Resource`) s'éditent directement sur la carte.
4. **Relier les ports** — en respectant les règles du §6 (l'éditeur refuse les
   liaisons interdites).
5. **Valider** — `Tools → Validate graph` (voir §8) et corriger les anomalies.
6. **Exporter** — `File → Export clean JSON…`.
7. **Simuler / visualiser** — `python visual_simulation.py mon_flux.json`.

---

## 5. Définir les modèles (`Models → Edit models…`)

Une table à **deux colonnes** :

| model name | parent model |
|---|---|
| `M1` | *(vide)* |
| `C1` | `M1` |
| `C2` | `M1` |

- **« Add »** ajoute une ligne, **« Remove selected »** en retire.
- Laissez **parent model vide** pour un modèle racine.
- Un parent cité doit exister (sinon erreur), et les noms doivent être **uniques**.

C'est ici que se construit la hiérarchie parent/enfant ; rappelez-vous (doc
simulation) que choisir un parent dans une *capability* ou un stock revient à
accepter **tous ses descendants**. Définissez vos modèles **au niveau auquel vous
voulez les distinguer** dans les stocks et les comptages.

---

## 6. Les règles de connexion (qui se branche sur quoi)

L'éditeur **valide chaque fil au moment où vous le tirez** : une liaison interdite
est immédiatement refusée avec un message « *Invalid connection* ». Voici la carte
des liaisons autorisées (sortie → entrée) :

| Carte source (sortie) | Se branche sur (entrée) |
|---|---|
| **Distribution** `distribution` | `Task.task_duration`, `Task.startup_duration`, `FirstTask.task_duration`, `Breakdown.mtbf`, `Breakdown.mttr`, `RestockableResource.order_duration`, `RestockableResource.delivery_duration` |
| **Interval** `interval` | `ScheduledShutdowns.intervals` |
| **ScheduledShutdowns** `scheduled_shutdowns` | `Task.scheduled_shutdowns` |
| **Resource** / **RestockableResource** `resource` | `Task.resources`, `Task.operators`, `Task.startup_operators`, `FirstTask.resources` |
| **HardBuffer** `to_task` | `Task.bufs_in` |
| **HardBuffer** `monitor` | `Monitor.buffer` *(observation seule, ne déplace pas de pièces)* |
| **Task** / **FirstTask** / **Breakdown** `bufs_out` | `HardBuffer.from_task`, `SoftBuffer.from_task` |
| **SoftBuffer** `to_buffers` | `HardBuffer.from_task`, `SoftBuffer.from_task` *(routage imbriqué possible)* |
| **Task** `task_ref` | `Breakdown.task` |

Quelques points utiles :

- Le **sens du flux des pièces** : `FirstTask`/`Task` → (`bufs_out`) → `HardBuffer`
  ou `SoftBuffer` → (`to_task`/`to_buffers`) → `Task` suivant. Tout le reste
  (distributions, ressources, intervalles, shutdowns, task_ref, monitor) ne
  transporte **pas** de pièces : ce sont des **branchements de paramétrage**.
- Beaucoup de ports sont **multiples** (un `Task` peut avoir plusieurs `bufs_in`,
  plusieurs opérateurs ; un `HardBuffer` peut alimenter plusieurs tâches).
- Les **boucles sont autorisées** (reprise/réparation : `Breakdown → Buffer → Task
  → Breakdown`). Le graphe n'est volontairement pas acyclique.

---

## 7. Les cartes, une par une

Pour chaque type : ses **ports** (E = entrée, S = sortie) et ce qui se **configure**
(double-clic, sauf mention « sur la carte »).

### Distribution
- **S** : `distribution`.
- **Double-clic** : choisir le **type** (`Constant`, `Normal`, `Triangular`,
  `Exponential`) et remplir ses **paramètres**. La carte se renomme automatiquement
  (« Constant distribution »…).
- Sert à alimenter **toutes les durées** du modèle (traitement, démarrage, MTBF,
  MTTR, commande, livraison, intervalle entre créations).

### Interval
- **S** : `interval`.
- **Sur la carte** : `start` et `end` (avec `start ≤ end`).
- Se branche dans un `ScheduledShutdowns`.

### Scheduled Shutdowns
- **E** : `intervals` (multiple). **S** : `scheduled_shutdowns`.
- Regroupe plusieurs `Interval` **disjoints** ; se branche sur `Task.scheduled_shutdowns`.

### Resource
- **S** : `resource`.
- **Sur la carte** : `capacity`, `anonymous`. (Double-clic : réglages détaillés.)
- L'opérateur/la machine réutilisable, partagé(e) entre postes.

### Restockable Resource
- **E** : `order_duration`, `delivery_duration`. **S** : `resource`.
- **Sur la carte** : `capacity`, `threshold`.
- Le consommable qui se vide et se recommande tout seul sous le seuil.

### Hard Buffer
- **E** : `from_task` (multiple). **S** : `to_task` (multiple), `monitor` (multiple).
- **Double-clic** : choisir les **modèles acceptés** (`valid_models`).
- **Sur la carte** : `role` = **Normal / Exit / Scrap**, et `capacity` (`inf` par
  défaut). Le rôle change l'affichage dans la visualisation (Normal = carrés des
  pièces ; Exit/Scrap = comptes seulement).

### Soft Buffer
- **E** : `from_task` (multiple). **S** : `to_buffers` (multiple).
- **Double-clic** : régler les **probabilités** vers chaque buffer connecté
  (à faire **après** avoir tiré les fils de sortie ; les probabilités sont
  mémorisées par buffer cible, pas par ordre). Elles doivent **sommer à 1**, et
  toutes les destinations doivent accepter **les mêmes modèles**.

### First Task (la source)
- **E** : `task_duration`, `resources` (multiple). **S** : `bufs_out` (multiple).
- **Double-clic** : table **modèle → probabilité** (somme = 1), puis, si des
  ressources sont connectées, leurs **quantités**.

### Task (le poste)
- **E** : `bufs_in` (multiple), `resources` (multiple), `operators` (multiple),
  `startup_operators` (multiple), `task_duration`, `startup_duration`,
  `scheduled_shutdowns`. **S** : `bufs_out` (multiple), `task_ref`.
- **Double-clic** (`TaskConfigDialog`) : **capability** (modèles traités),
  **portées** `resources_scope` / `operators_scope`, **quantités** de chaque
  ressource/opérateur, **min_capacity** / **max_capacity**, **batch_collector**
  (`Greedy` / `Altruistic`), **independent_carriers**.
- ⚠️ Rappels de portée : opérateurs jamais `PER_PIECE` ; consommables jamais
  `PER_TASK`. Les sorties doivent **partitionner** la capability.

### Breakdown (la panne)
- **E** : `task`, `mtbf`, `mttr`. **S** : `bufs_out` (multiple).
- Câblage : `Task.task_ref → Breakdown.task`, une `Distribution` sur `mtbf`, une
  sur `mttr`, et `bufs_out` vers le(s) stock(s) de **rebut/reprise**.

### Monitor (la mesure)
- **E** : `buffer` (depuis `HardBuffer.monitor`).
- **Sur la carte** : une **case à cocher par statistique** (longueur moyenne/max,
  temps de séjour, débit…). Seuls les `HardBuffer` sont mesurables.

---

## 8. Valider avant d'exporter (`Tools → Validate graph`)

La validation liste les problèmes les plus courants, par exemple :

- une **liaison interdite** subsistante ;
- un `Task` **sans buffer d'entrée**, sans `task_duration`/`startup_duration`, ou
  **sans capability** ;
- un `FirstTask` dont les probabilités **ne somment pas à 1** ou sans `task_duration` ;
- un `HardBuffer` **sans `valid_models`** ;
- un `SoftBuffer` sans buffer de sortie ou dont les probabilités ne somment pas à 1 ;
- une `Distribution` aux paramètres invalides ; une `RestockableResource` sans
  `delivery_duration`.

Corrigez ces points : ils correspondent exactement aux **règles** rappelées dans la
doc de la simulation (et certaines feront **échouer la construction** du modèle si
elles ne sont pas respectées).

> À noter : la validation signale aussi des oublis de câblage, mais elle ne vérifie
> pas tout (par ex. la *partition* des sorties est contrôlée au démarrage de la
> simulation). Faire un essai avec `visual_simulation.py` reste le meilleur test.

---

## 9. Exporter, ré-importer

- **File → Export clean JSON…** écrit le flux (cartes, positions, connexions,
  modèles, *backdrops*) dans un fichier `.json`.
- **File → Import clean JSON…** recharge un flux exporté, **aux mêmes positions** (et
  les *backdrops* à leur taille d'origine).
- La sauvegarde de session NodeGraphQt n'est **pas** utilisée : seul ce JSON propre
  fait foi.

---

## 10. Petits plus pour s'organiser

- **Templates → Add Backdrop Around Selection** : encadre les cartes sélectionnées
  dans un panneau titré (pratique pour grouper visuellement un poste et toute sa
  configuration). Les *backdrops* sont exportés et réimportés tels quels.
- **Tools → Auto-layout selected** : range automatiquement les cartes
  sélectionnées ; **Frame all** recadre la vue sur l'ensemble du flux.
- **Edit → Delete selected** (touche **Suppr**) : supprime les cartes choisies.

---

## 11. Et ensuite ?

Une fois le flux exporté :

```bash
python visual_simulation.py mon_flux.json      # voir l'animation
python graph_parser.py                          # statistiques (via les Monitors)
```

Observez l'animation pour **vérifier que les pièces circulent comme prévu** et
repérer les stocks qui gonflent. Si un comportement vous surprend, revenez à la doc
de la simulation (`documentation_simulation_fr.md`) pour le concept, puis ajustez le
réglage de la carte concernée dans l'éditeur.
