# Guide d'utilisation du Flow Designer

Le Flow Designer est l'application graphique servant à construire les modèles de simulation, à les exécuter, et à en explorer les résultats. Ce guide couvre l'ensemble des fonctionnalités.

**Prérequis :** la [référence de la simulation](simulation.fr.md). Ce guide emploie ses concepts (pièce, modèle, poste, carrier, buffer, opérateur, shift) sans les redéfinir.

---

## 1. Principe de fonctionnement

Un modèle se construit sous forme de diagramme. Chaque station, buffer et source est une **carte** sur un canevas ; des **fils** entre les cartes définissent la circulation des pièces. Les réglages d'une carte s'éditent dans une boîte de dialogue ouverte par double-clic. Les définitions partagées par tout le modèle (modèles produits, groupes d'opérateurs, plannings) sont gérées dans des **registres** plutôt que sur les cartes individuelles. Une fois le modèle complet, il est validé, exécuté, et les résultats s'examinent directement sur le diagramme.

Le flux de travail est : construire, configurer, exécuter, analyser.

---

## 2. Navigation sur le canevas

- **Déplacement de la vue :** glisser le fond du canevas avec le bouton de la molette, ou en maintenant Alt et en glissant sur une zone vide.
- **Zoom :** molette de la souris.
- **Sélection :** cliquer une carte ; tracer un rectangle, ou Maj + clic (Shift + clic) pour une sélection multiple.
- **Déplacement de cartes :** glisser la sélection. Le placement des cartes est purement visuel ; seul le câblage affecte la simulation.
- **Frame all** (menu Tools) : ajuste la vue à l'ensemble du modèle.

La configuration des cartes s'effectue par les boîtes de réglages décrites en section 6.

---

## 3. Création de cartes

Le menu **Create** insère une nouvelle carte au centre de la vue courante. Chaque entrée correspond à un concept de la simulation :

| Entrée de menu | Composant |
|---|---|
| Piece generator | La source modulable de pièces. |
| Buffer | Une file : passage entre deux tâches, sortie, ou rebut. |
| Router | Un embranchement probabiliste, typiquement pour le tri qualité. |
| Piece task | Une station traitant des pièces. |
| Resource task | Une station transformant des matières. |
| Shutdowns | Un arrêt planifié, rattaché à un poste. |
| Breakdown | Une panne aléatoire, rattachée à un poste. |

Une carte neuve porte des réglages par défaut. Le renommage et la configuration s'effectuent par sa boîte de réglages (section 6).

---

## 4. Le câblage

Les cartes exposent des **ports** sur leurs bords. Glisser d'un port de sortie vers un port d'entrée crée un fil. Les fils définissent la circulation des pièces et le rattachement des interruptions.

Le designer impose la validité des connexions : seules les combinaisons cohérentes peuvent être câblées. Les connexions invalides sont rejetées dès le tracé.

Connexions typiques :

- Générateur vers buffer(s) : où arrivent les nouvelles pièces.
- Buffer(s) vers poste : l'entrée d'une station.
- Poste vers buffer(s) ou routeur(s) : la sortie d'une station.
- Routeur vers buffers : les branches de l'embranchement.
- Shutdowns vers poste et breakdown vers poste : rattachement des interruptions.

```mermaid
flowchart LR
    G([Générateur]) --> B1[Buffer]
    B1 --> T[Poste à pièces]
    T --> R{Routeur}
    R --> B2[Buffer : accepté]
    R --> B3[Buffer : retouche]
    R --> SC[Buffer : rebut]
    B2 --> T2[Poste aval]
    T2 --> EX[Buffer : sortie]
    SD[Shutdowns] -.rattaché.-> T
    BD[Breakdown] -.rattaché.-> T
```

Deux buffers ne se relient jamais directement ; une tâche s'intercale toujours entre eux. Un routeur peut alimenter plus de deux buffers.

Pour retirer un fil, saisir sa tête (l'extrémité fléchée) et la relâcher dans le vide : le fil disparaît. Les fils ne se sélectionnent pas.

---

## 5. Les registres

Les définitions partagées se gèrent dans le menu **Registries**. Les cartes référencent les entrées de registre par leur nom ; les registres se remplissent donc généralement avant les cartes qui les utilisent.

### Les modèles

**Registries, Edit models.** Les modèles produits et leur hiérarchie. Chaque entrée a un nom et un parent optionnel. Tous les sélecteurs de modèles des boîtes de cartes puisent dans ce registre.

> **Note.** Un parent doit être déclaré avant ses enfants. Dans le champ Parent d'un enfant, saisir le nom du parent tel quel (verbatim) ; c'est ce nom qui établit le lien.

### Les ressources

**Registries, Edit resources.** Les matières : capacité, quantité initiale, durée de vie, et pour les ressources réapprovisionnables le seuil, la durée de commande et la durée de livraison.

> **Note.** Le Flow Designer n'impose aucune unité. Toutes les quantités d'une ressource (capacité et quantité initiale du registre, quantités demandées par les tâches, quantités produites) sont des nombres purs, interprétés dans l'unité choisie par l'utilisateur. La cohérence est à sa charge : pour une même ressource, employer la même unité partout. Les valeurs des rapports sont exprimées dans cette même unité. Il est recommandé de consigner les unités retenues.

### Les opérateurs

**Registries, Edit operators.** Les équipes : effectif, shifts (sélectionnés dans le registre des shifts), et productivité.

### Les shifts

**Registries, Edit shifts.** Les plannings, en mode hebdomadaire ou personnalisé, avec des jours de fermeture optionnels tirés du registre des jours de fermeture.

L'éditeur de shifts fournit deux fonctions de productivité :

- **Translation :** créer un nouveau shift comme copie décalée dans le temps d'un shift existant.
- **Répétition :** dupliquer un shift vers l'avant un nombre spécifié de fois avec une translation calendaire (années, mois, semaines, jours). Un motif annuel se définit une fois et se répète sur l'horizon ; les années bissextiles sont gérées, et chaque copie porte ses jours de fermeture décalés à la période correspondante.

> **Note.** Il est donc inutile de créer des jours de fermeture pour les années ultérieures d'un shift répété. Définir les fériés d'une seule année (par exemple 2026) suffit : la répétition en déduit automatiquement les fériés des années suivantes, décalés à la période correspondante.

#### Les shifts qui franchissent minuit

Un shift hebdomadaire dont l'horaire déborde sur le lendemain, par exemple lundi 22h à mardi 6h, se crée de deux façons selon le comportement souhaité face aux jours fériés. L'heure de fin peut dépasser 24 : `30:00` signifie 6h le lendemain matin.

| Méthode (mode hebdomadaire) | Saisie | Comportement si le lendemain est férié |
|---|---|---|
| Intervalle unique | Lundi `22:00 -> 30:00` | La nuit du lundi reste **complète** (jusqu'à mardi 6h). Le férié du mardi retire seulement la nuit du mardi. |
| Découpage à minuit | Lundi `22:00 -> 24:00` **et** mardi `00:00 -> 06:00` | La nuit du lundi est **coupée à minuit** (le morceau du mardi, posé sur le jour férié, est retiré). |

Retenir l'intervalle unique lorsque l'équipe doit terminer sa nuit malgré un férié le lendemain ; retenir le découpage à minuit lorsqu'aucune activité ne doit avoir lieu le jour férié.

### Les jours de fermeture

**Registries, Edit closing days.** Une liste partagée de dates de fermeture (fériés, fermetures d'usine). Les shifts sélectionnent leurs jours de fermeture dans cette liste ; chaque date est ainsi définie une seule fois.

---

## 6. Configuration des cartes

Double-cliquer une carte ouvre sa boîte de réglages. Les réglages correspondent directement à la référence de la simulation ; cette section liste le contenu de chaque boîte.

### Piece generator (générateur)

- **Shifts :** le planning d'émission.
- Le câblage de sortie détermine les buffers de destination.

Les modèles émis et leurs objectifs ou débits ne se configurent pas sur la carte ; ils font partie de **Simulation, Settings** (section 7), car ils sont liés au critère d'arrêt. La carte générateur définit quand et où les pièces sont émises ; les réglages de simulation définissent quoi et en quelle quantité.

### Buffer

- **Type de buffer :** passage, sortie, ou rebut.
- **Modèles valides.**

### Router (routeur)

- **Probabilités de branche**, une par buffer sortant ; optionnellement une branche freeloader. Les valeurs peuvent être des constantes ou des fonctions du temps.

### Piece task (poste à pièces)

- **Configs de modèles :** par modèle géré, la durée de traitement, les tailles de lot (capacités minimale et maximale du carrier), et les ressources consommées.
- **Durées du poste :** mise en route et chargement.
- **Opérateurs :** alternatives pour la mise en route, le chargement et le traitement ; scope opérateur.
- **Réglages de carriers :** capacité max, carriers minimum, contigus, indépendants.
- **Type de collecteur** et règle de modèle focus. La règle de modèle focus n'a d'effet que si le collecteur est discriminant.
- **Timeout, priorité, drapeau admin.**
- **Protocoles :** les sélections de protocoles (contraintes de shift, traitement des carriers en attente avant un arrêt, conscience de soi des opérateurs, ordre de sortie des pièces). Les valeurs par défaut conviennent à la plupart des stations. Dans la boîte, cet onglet est intitulé **Protocols**.
- **Shifts du poste :** le planning d'exploitation de la station, c'est-à-dire son temps d'ouverture.

Pour une première passe, les configs de modèles, les opérateurs et les shifts du poste sont généralement les seuls réglages qui demandent attention.

### Resource task (poste à ressources)

- **Ressources non transformées**, **ressources transformées** (avec proportions et drapeaux récupérables), **ressources de sortie** (lois bornées).
- **Durée** et le choix de collecteur greedy ou altruiste.
- Opérateurs, réglages de carriers, timeout, priorité et shifts comme pour les postes à pièces.

### Shutdowns (arrêt programmé)

- **Type :** flexible ou non flexible.
- **Planning :** intervalles explicites, ou génération périodique (intervalle, durée, plage de dates).
- Câbler la carte au poste concerné.

### Breakdown (panne)

- **MTBF** et **MTTR**, chacun une loi.
- Pour une panne sur un poste à pièces, câbler ses sorties vers les **buffers canots de sauvetage** qui reçoivent les pièces en cours lors d'une défaillance. Les pannes sur postes à ressources n'ont pas de sorties.
- Câbler la carte au poste concerné.

---

## 7. Les réglages de simulation

**Simulation, Settings** contient la configuration au niveau de l'exécution :

- **Date de début :** l'ancre calendaire.
- **Graine (seed) :** la graine aléatoire. Une graine et un modèle donnés reproduisent exactement la même exécution sur un moteur donné.

  > **Note.** Une même graine ne produit pas le même résultat sur les deux moteurs. Les générateurs de nombres aléatoires de Python et de C++ diffèrent ; les résultats des deux moteurs sont statistiquement comparables mais pas identiques.

- **Critère d'arrêt**, qui définit également l'émission du générateur :
  - **Par pièces produites (mode objectifs) :** un objectif par modèle feuille, un gap manuel ou une période de grâce pour le gap automatique, et un timeout.
  - **Par le temps (mode débit) :** une probabilité par modèle (l'une peut être le freeloader), un gap, et la date de fin.

---

## 8. Désactivation de cartes

Les cartes sélectionnées peuvent être désactivées via **Edit, Disable / enable cards** (également disponible dans le menu contextuel). Une carte désactivée reste sur le canevas, grisée, avec son câblage intact, mais elle est entièrement exclue de la validation et de l'exécution : la carte, ses connexions, et toutes les références à elle sont retirées avant la construction de la simulation.

La désactivation permet le test partiel d'un flux (isoler une section de la ligne) et la mise de côté temporaire de stations inachevées sans les supprimer. La réactivation restaure les cartes à l'identique.

---

## 9. Gestion des fichiers

- **File, New :** modèle vide. Les changements non sauvegardés déclenchent une confirmation.
- **File, Open :** charger un fichier de modèle, en remplacement de la session courante.
- **File, Save / Save as :** écrire le modèle. La barre de titre signale les changements non sauvegardés.

Un fichier de modèle est autonome : il inclut les cartes, le câblage, les registres et les réglages de simulation. Partager un modèle revient à partager un fichier.

---

## 10. La validation

**Tools, Validate graph** analyse le modèle sans l'exécuter et signale les problèmes : postes sans entrées ou sorties, buffers en impasse, références d'opérateurs ou de shifts manquantes, probabilités incohérentes, contraintes de capacité menant au blocage, buffer de sortie manquant, et critère mal configuré.

La validation s'exécute aussi automatiquement avant chaque exécution, avec la possibilité de poursuivre malgré les avertissements. Les cartes désactivées sont exclues de la validation.

---

## 11. Exécuter une simulation

**Simulation, Run simulation** (F5) :

1. Le modèle est sauvegardé (l'exécution exécute le fichier sur disque).
2. La validation s'exécute ; les avertissements sont présentés avant le démarrage.
3. La fenêtre de progression s'ouvre et l'exécution démarre.

### Sélection du moteur

**Simulation, Engine** sélectionne le moteur d'exécution :

- **Python :** le moteur de référence.
- **C++ (native) :** un moteur nettement plus rapide. Un binaire préconstruit est fourni par plateforme ; un exécutable personnalisé peut être désigné via **Select C++ executable**.

Les deux moteurs produisent les mêmes fichiers de sortie avec la même structure.

> **Note.** À graine égale, les deux moteurs ne produisent pas des valeurs identiques : leurs générateurs aléatoires diffèrent. Les résultats restent statistiquement comparables. Le choix du moteur affecte la vitesse d'exécution et, à la marge, la réalisation aléatoire obtenue.

### La fenêtre de progression

Pendant l'exécution, la fenêtre affiche la date simulée courante, le compte de sorties, la progression vers l'objectif ou la date de fin, et le temps réel écoulé. À la fin de la simulation, la fenêtre entre dans une phase **Generating outputs** (barre de progression indéterminée) pendant l'écriture des rapports, tableaux et graphiques ; sur les grandes exécutions, cette phase prend plusieurs secondes.

À l'achèvement, la fenêtre présente le résultat (objectif atteint, date de fin atteinte, timeout), le chemin du dossier de rapport, et les actions **Open report folder** et **View results**.

---

## 12. Le mode résultats

**View results** après une exécution, ou **Results, Open run results** pour une exécution antérieure, bascule le designer en mode résultats :

- Le canevas est verrouillé contre l'édition : ni les cartes ni les câblages ne peuvent être modifiés.
- Le double-clic sur une carte ouvre ses indicateurs : production et attentes pour un poste, statistiques de file pour un buffer, occupation pour un groupe d'opérateurs. Le double-clic sur la carte **Piece generator** ouvre les indicateurs de la ligne : flux global, production par modèle (objectif, généré, sorties, rebut, atteinte) et trajectoires.
- Un panneau inférieur présente les tableaux couvrant toute l'exécution.
- Un contrôle de carte de chaleur colore les cartes selon un indicateur sélectionné, fournissant une vue immédiate de la répartition de charge et des goulots.
- **Exit results mode** revient à l'édition.

Le diagramme affiché est le modèle exact qui a été exécuté ; les indicateurs s'affichent sur les composants qu'ils décrivent.

---

## 13. Les sorties d'exécution

Chaque exécution écrit un dossier sous `runs/`, nommé par la date et le nom du fichier de modèle, contenant :

- Des rapports CSV par poste, par buffer, par groupe d'opérateurs et par ressource.
- Les totaux de la ligne : production, rebut, temps de traversée, encours.
- Un dossier `graphes/` de graphiques, chacun fourni en PNG et en données CSV sous-jacentes.
- Une copie du modèle exécuté et un fichier d'identité de l'exécution (source, dates, graine, temps de calcul, critère d'arrêt), rendant chaque exécution reproductible et autonome.

Tous les fichiers CSV s'ouvrent directement dans Excel. La description complète de chaque fichier, de chaque indicateur, et des conventions de mesure se trouve dans la **[référence des KPI](kpis.fr.md)**.
