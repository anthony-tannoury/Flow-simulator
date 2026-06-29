"""
================================================================================
SIMULATION D'UN ATELIER DE PRODUCTION INDUSTRIELLE
================================================================================

À QUOI SERT CE PROGRAMME
------------------------
Ce programme imite (« simule ») le fonctionnement d'un atelier de production :
des pièces arrivent, passent par une ou plusieurs machines/postes de travail,
y subissent une opération (usinage, injection, marquage, contrôle...), puis
repartent vers la suite de la ligne ou vers un stock de sortie.

Le but est de pouvoir répondre, SANS toucher à l'atelier réel, à des questions
du type :
  - Combien de pièces produit-on en une journée / une semaine ?
  - Que se passe-t-il si une machine tombe en panne plus souvent ?
  - Combien de stock faut-il garder pour ne jamais être à court de matière ?
  - Les arrêts planifiés (maintenance, pauses, nuit) coûtent-ils beaucoup ?
  - Faut-il plus d'opérateurs ? De plus grandes machines ?

On fait « tourner » l'atelier dans l'ordinateur sur une durée choisie, puis on
regarde les résultats (pièces produites, files d'attente, temps d'arrêt...).

LE TEMPS DANS LA SIMULATION
---------------------------
Le temps n'avance pas en continu : il « saute » d'un évènement au suivant
(une pièce arrive, une machine finit, une panne survient...). C'est ce qu'on
appelle une simulation à évènements discrets. L'unité de temps est libre : si
vous donnez des durées en minutes, alors tout est en minutes.

LES GRANDS INGRÉDIENTS (vue d'ensemble)
---------------------------------------
  - MODÈLE (Model)        : le « type » d'une pièce (ex. famille moteur M88,
                            LEAP...). Les modèles peuvent être organisés en
                            familles (un type général qui se décline en
                            sous-types).
  - PIÈCE (Piece)         : un exemplaire physique qui traverse l'atelier.
  - BUFFER (Buffer)       : une zone de stockage / file d'attente entre deux
                            postes (un « stock tampon »).
  - ROUTEUR (Router)      : un aiguillage : envoie une pièce vers telle ou telle
                            destination selon des probabilités (ex. 90 % vont au
                            poste suivant, 10 % partent en retouche).
  - RESSOURCE RÉAPPRO.    : une matière consommable qui se recommande toute seule
    (RestockableResource)   quand le stock devient bas (ex. cire, consommables).
  - PANNE (Breakdown)     : arrêt SUBI et aléatoire d'un poste (imprévisible).
  - ARRÊT PLANIFIÉ        : arrêt PRÉVU à l'avance (maintenance, nuit, week-end).
    (ScheduledShutdown)
  - POSTE / TÂCHE (Task)  : une machine ou un poste de travail qui transforme
                            les pièces. C'est le cœur de l'atelier.
  - CHARIOT (Carrier)     : voir l'explication détaillée plus bas — c'est la
                            notion la plus importante à comprendre.

Tout ce qui suit est construit au-dessus de la bibliothèque « salabim », qui
fournit la mécanique du temps, des files et des ressources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from enum import Enum, auto
from typing import override, Callable

import numpy as np
import salabim as sim

#########
# SETUP #
#########

# SEED = « graine » du générateur de hasard. Tant que la graine ne change pas,
# la simulation rejoue EXACTEMENT la même séquence d'évènements aléatoires
# (mêmes pannes, mêmes durées tirées au sort...). C'est indispensable pour
# comparer deux scénarios « toutes choses égales par ailleurs » et pour
# reproduire un bug. Changez la graine pour obtenir un autre tirage du hasard.
SEED = 42
sim.yieldless(True)                              # mode de fonctionnement de salabim
env = sim.Environment(random_seed=SEED)          # l'« horloge » et le moteur de simulation
np.random.seed(SEED)                             # même graine pour les tirages numpy


###########
# HELPERS #
###########

class Interval:
    """
    Un INTERVALLE DE TEMPS [début, fin]. Sert à décrire une période, par exemple
    une fenêtre d'arrêt planifié (« de la minute 480 à la minute 600 »).
    """

    def __init__(self, start: float, end: float) -> None:
        # On refuse un intervalle qui finit avant de commencer (ce serait absurde).
        if start > end:
            raise ValueError("Interval start must be less than or equal to end")
        self.start = start    # instant de début
        self.end = end        # instant de fin

    @staticmethod
    def disjoint(int1: Interval, int2: Interval) -> bool:
        # Deux intervalles sont « disjoints » s'ils ne se chevauchent pas du tout.
        # Utile pour vérifier que deux arrêts planifiés ne se superposent pas.
        if int1.start > int2.start:
            int1, int2 = int2, int1
        return int1.end < int2.start


def check_probs(probs: list[float]) -> None:
    """
    Vérifie qu'une liste de probabilités est cohérente : chacune entre 0 et 1,
    et leur somme égale à 1 (100 %). Sert pour les aiguillages et la génération
    de pièces, où l'on répartit des choix en pourcentages.
    """
    if not all(0 <= p <= 1 for p in probs):
        raise ValueError("Probabilities must be in [0,1]")

    if abs(sum(probs) - 1) > 1e-6:
        raise ValueError("Probabilities must sum to 1")


def check_inlet_validity(receiver: PickyPieceTaker, inlets: list[Buffer]) -> None:
    """
    Vérifie qu'un poste est bien capable de traiter TOUS les modèles de pièces
    qui peuvent arriver par ses entrées. On veut éviter qu'une pièce se retrouve
    devant une machine incapable de la traiter (situation bloquante).
    """
    if not inlets:
        raise ValueError("Receiver must have at least one inlet")

    if not all(inlet.can_flush_into(receiver) for inlet in inlets):
        raise ValueError("Receiver must be able to receive all models from inlets")


def check_outlet_validity(giver: PickyPieceTaker, outlets: list[Outlet]) -> None:
    """
    Vérifie la cohérence des SORTIES d'un poste :
      - les sorties ne doivent pas se « disputer » les mêmes modèles (chaque
        modèle de pièce a une destination claire, pas d'ambiguïté) ;
      - toute pièce que le poste peut produire doit avoir une sortie qui l'accepte
        (sinon, en fin d'opération, on ne saurait pas où la déposer).
    """
    if not outlets:
        raise ValueError("Giver must have at least one outlet")

    for i in range(len(outlets)):
        for j in range(i + 1, len(outlets)):
            if not PickyPieceTaker.disjoint(outlets[i], outlets[j]):
                raise ValueError("Outlets must have disjoint valid models sets")

    valid_models_sets = [set(outlet.valid_models) for outlet in outlets]
    union = set.union(*valid_models_sets)

    if not giver.can_flush_into(PickyPieceTaker(list(union))):
        raise ValueError("Giver must be able to flush all models into outlets")


def place(pieces: list[Piece], outlets: list[Outlet]) -> None:
    """
    DÉPOSE une liste de pièces dans les bonnes destinations de sortie.
    Pour chaque pièce, on parcourt les sorties et on la met dans la première qui
    l'accepte (selon son modèle). C'est ce qui fait avancer une pièce d'un poste
    vers le suivant (ou vers un stock de sortie).
    """
    for piece in pieces:
        placed = False

        for outlet in outlets:
            buffer = outlet.get()
            if buffer.can_take(piece):
                piece.enter(buffer)
                placed = True
                break

        assert placed, "Could not place piece in outlets"


#########
# MODEL #
#########

class Model:
    """
    UN TYPE DE PIÈCE, éventuellement organisé en arbre de familles.

    Exemple : un modèle « Moteur » (général) peut avoir pour enfants « M88 » et
    « LEAP ». Une pièce concrète porte toujours un modèle « feuille » (le plus
    précis, sans enfant). L'intérêt de l'arbre : un poste peut déclarer accepter
    « Moteur » et acceptera alors automatiquement M88 ET LEAP.

    Paramètres :
      - name      : nom lisible du modèle (ex. "M88").
      - parent    : le modèle plus général au-dessus (ou None si racine).
      - children  : la liste des sous-modèles (vide si c'est une feuille).
    """

    def __init__(self, name: str, parent: Model | None, children: list[Model]):
        self.name = name
        self.parent = parent
        self.children = children

    def __repr__(self):
        return self.name


########################
# RESTOCKABLE RESOURCE #
########################

class Delivery(sim.Component):
    """
    UNE LIVRAISON en cours. Quand une commande de réapprovisionnement est passée,
    cette « livraison » attend le délai d'acheminement puis remet le stock à
    niveau. Elle vit sa vie de façon autonome : même si le poste qui a déclenché
    la commande tombe en panne entre-temps, la livraison arrive quand même.

    Paramètres :
      - stock              : la matière à recompléter.
      - delivery_duration  : le délai d'acheminement (tiré au sort à chaque fois).
    """

    def setup(self, stock: RestockableResource, delivery_duration: sim.Distribution) -> None:
        self.stock = stock
        self.delivery_duration = delivery_duration

    def process(self):
        # On attend le délai de livraison, puis on remplit le stock jusqu'au max,
        # et on signale que la commande en cours est terminée.
        self.hold(self.delivery_duration.sample())
        missing = self.stock.capacity.value - self.stock.available_quantity()
        self.request((self.stock, -missing))
        self.stock.active_order = False


class RestockableResource(sim.Resource):
    """
    UNE MATIÈRE / CONSOMMABLE QUI SE RECOMMANDE TOUTE SEULE (ex. cire, colle,
    petites fournitures). On en consomme à chaque opération ; quand le niveau
    passe sous un SEUIL, une commande est automatiquement passée.

    Le réapprovisionnement se fait en deux temps, comme dans la vraie vie :
      1) on passe la commande : cela occupe quelqu'un pendant une « durée de
         commande » (order_duration) ;
      2) la marchandise arrive plus tard, après une « durée de livraison »
         (delivery_duration), gérée par une Delivery indépendante.

    Paramètres :
      - order_duration     : temps pour PASSER la commande (occupe le demandeur).
      - delivery_duration  : délai entre la commande et l'arrivée de la matière.
      - threshold          : SEUIL de déclenchement. Dès que le stock disponible
                             passe en dessous, on recommande. Plus il est haut,
                             plus on commande tôt (sécurité), mais plus souvent.
    """

    def __init__(self, *args, **kwargs) -> None:
        # « anonymous » : on peut ajouter/retirer de la quantité sans qu'un
        # « propriétaire » précis ne la détienne (c'est du stock, pas un outil
        # qu'on emprunte et qu'on rend).
        kwargs["anonymous"] = True
        super().__init__(*args, **kwargs)

    def setup(self, order_duration: sim.Distribution, delivery_duration: sim.Distribution, threshold: float) -> None:
        self.order_duration = order_duration
        self.delivery_duration = delivery_duration
        self.threshold = threshold
        self.active_order = False    # y a-t-il déjà une commande en cours ?

    def restock(self, demander: sim.Component):
        """
        Déclenche un réapprovisionnement SI (et seulement si) il n'y a pas déjà
        une commande en cours ET que le stock est sous le seuil. Le « demandeur »
        (le poste ou le chariot qui consomme) est occupé le temps de passer la
        commande ; ensuite une livraison autonome prend le relais.
        """
        if not self.active_order and self.available_quantity() < self.threshold:
            demander.hold(self.order_duration.sample())
            self.active_order = True
            Delivery(stock=self, delivery_duration=self.delivery_duration)


#########
# PIECE #
#########

class Piece(sim.Component):
    """
    UNE PIÈCE PHYSIQUE qui circule dans l'atelier. Elle porte un modèle (son type)
    et un identifiant unique. C'est l'objet que l'on compte en sortie pour
    mesurer la production.

    Paramètre :
      - model : le type de la pièce. DOIT être un modèle « feuille » (le plus
                précis, sans sous-types).
    """

    ID = 0    # compteur global, sert à donner un numéro unique à chaque pièce

    def setup(self, model: Model) -> None:
        if model.children:
            raise ValueError("Piece model must be a leaf model")

        self.model = model
        self.id = str(Piece.ID).zfill(6)
        Piece.ID += 1


class PickyPieceTaker:
    """
    « PRENEUR DIFFICILE » : tout élément qui n'accepte que CERTAINS modèles de
    pièces (un poste, une zone de stockage, une sortie...). Il connaît la liste
    des modèles qu'il accepte et sait dire, pour une pièce donnée, s'il la prend.

    Grâce à l'arbre des modèles, accepter une famille revient à accepter tous ses
    sous-modèles : déclarer « Moteur » accepte automatiquement M88 et LEAP.

    Paramètre :
      - valid_models : liste des modèles (familles ou feuilles) acceptés.
    """

    def __init__(self, valid_models: list[Model]) -> None:
        if not valid_models:
            raise ValueError("PickyPieceTaker must have at least one valid model")

        self.valid_models = valid_models

    def can_take(self, obj: Piece | Model) -> bool:
        # Une pièce est acceptée si son modèle, OU n'importe lequel de ses
        # modèles parents (en remontant la famille), figure dans la liste acceptée.
        model = obj.model if isinstance(obj, Piece) else obj
        can_take = False
        while model is not None and not can_take:
            can_take |= model in self.valid_models
            model = model.parent
        return can_take

    def can_flush_into(self, ppt: PickyPieceTaker) -> bool:
        # « Est-ce que tout ce que MOI je peux contenir est accepté par l'autre ? »
        # Sert à vérifier qu'un flux de pièces peut bien passer d'un élément à un autre.
        return all(ppt.can_take(model) for model in self.valid_models)

    @staticmethod
    def disjoint(ppt1: PickyPieceTaker, ppt2: PickyPieceTaker) -> bool:
        # Deux preneurs sont « disjoints » s'ils n'acceptent aucun modèle en commun.
        return not (any(ppt1.can_take(model) for model in ppt2.valid_models)
                    or any(ppt2.can_take(model) for model in ppt1.valid_models))


##########
# BUFFER #
##########

class Outlet(PickyPieceTaker, ABC):
    """
    UNE SORTIE : une destination possible pour les pièces en fin d'opération.
    Notion abstraite : une sortie peut être directement une zone de stockage
    (Buffer) ou un aiguillage (Router) qui redirige.
    """

    def __init__(self, valid_models: list[Model]) -> None:
        super().__init__(valid_models)

    @abstractmethod
    def get(self) -> Buffer:
        # Renvoie la zone de stockage concrète où la pièce finira réellement.
        pass


class Buffer(sim.Store, Outlet):
    """
    UNE ZONE DE STOCKAGE / FILE D'ATTENTE entre deux postes (un « stock tampon »).
    Les pièces y attendent d'être prises par le poste suivant. Sert aussi de
    point d'entrée de l'atelier (où le générateur dépose) et de point de sortie
    (où l'on compte la production).

    Paramètre :
      - valid_models : les modèles que ce stock peut contenir.
    """

    def setup(self, valid_models: list[Model]) -> None:
        Outlet.__init__(self, valid_models)

    @override
    def get(self) -> Buffer:
        return self


class Router(Outlet):
    """
    UN AIGUILLAGE PROBABILISTE. Au lieu d'envoyer toutes les pièces au même
    endroit, il répartit le flux entre plusieurs destinations selon des
    pourcentages. Exemple typique : 90 % des pièces continuent vers le poste
    suivant, 10 % partent en boucle de retouche.

    Paramètre :
      - outlets_probs : un dictionnaire { destination : probabilité }. Les
                        probabilités doivent sommer à 1 (100 %). Toutes les
                        destinations doivent partager au moins un modèle commun.
    """

    def __init__(self, outlets_probs: dict[Outlet, float]) -> None:
        check_probs(outlets_probs.values())

        valid_models_sets = [set(outlet.valid_models) for outlet in outlets_probs.keys()]
        intersection = set.intersection(*valid_models_sets)

        if not intersection:
            raise ValueError("Router outlets must have at least one valid model in common")

        Outlet.__init__(self, list(intersection))
        self.outlets = list(outlets_probs.keys())
        self.probs = list(outlets_probs.values())

    @override
    def get(self) -> Buffer:
        # À chaque pièce, on tire au sort une destination selon les pourcentages.
        return self.outlets[np.random.choice(len(self.outlets), p=self.probs)].get()


################
# INTERRUPTERS #
################

class Breakdown(sim.Component):
    """
    LES PANNES (arrêts SUBIS et imprévisibles d'un poste).

    Comment ça marche, conceptuellement : le poste accumule un « risque » au fil
    du temps ; quand ce risque dépasse un seuil tiré au sort, la panne survient.
    Le taux de panne peut varier dans le temps (failure_rate est une fonction du
    temps), ce qui permet de représenter la fameuse « courbe en baignoire » :
    beaucoup de pannes au rodage, peu en régime stable, de plus en plus en fin de
    vie.

    Quand une panne survient :
      - le poste s'arrête immédiatement ;
      - les lots en cours sont évacués vers des sorties de secours (outlets) :
        les pièces ne sont pas perdues, elles sont déposées ailleurs ;
      - le poste reste en panne pendant une durée de réparation (mttr) ;
      - puis il redémarre.

    Paramètres :
      - task          : le poste concerné par ces pannes.
      - failure_rate  : fonction donnant le taux de panne à un instant t. Plus il
                        est élevé, plus les pannes sont fréquentes. Constant =
                        pannes au rythme régulier ; croissant = usure.
      - mttr          : « Mean Time To Repair » — la durée de réparation (tirée au
                        sort à chaque panne).
      - outlets       : sorties de SECOURS où évacuer les pièces d'un lot
                        interrompu par la panne.
    """

    MAX_ITERS = 60000    # garde-fou de calcul (évite une boucle infinie)

    def setup(self, task: PieceTask, failure_rate: Callable[[float], float], mttr: sim.Distribution,
              outlets: list[Outlet]) -> None:
        check_outlet_validity(task, outlets)
        self.task = task
        self.failure_rate = failure_rate
        self.mttr = mttr
        self.outlets = outlets

    def get_next_breakdown_time(self) -> float:
        # Calcule l'instant de la prochaine panne en accumulant le risque dans le
        # temps jusqu'à franchir un seuil tiré au hasard.
        threshold = -np.log(env.random.random())
        integral = 0
        t = env.now()
        dt = 60
        for _ in range(Breakdown.MAX_ITERS):
            if integral < threshold:
                integral += self.failure_rate(t) * dt
                t += dt
            else:
                return t
        raise ValueError(f"Integral did not cross threshold after {Breakdown.MAX_ITERS} iterations")

    def process(self):
        while True:
            # On ne « compte » pas de pannes pendant un arrêt planifié : une
            # machine éteinte ne tombe pas en panne. On attend donc qu'elle tourne.
            self.wait((self.task.is_in_shutdown, False))

            next_breakdown_time = self.get_next_breakdown_time()
            self.hold(till=next_breakdown_time)

            # Si un arrêt planifié a démarré entre-temps, on annule cette panne.
            if self.task.is_in_shutdown.get():
                continue

            # Déclenchement de la panne : on coupe le poste...
            self.task.started_up = False
            if self.task.task_starter is not None:
                self.task.task_starter.done.set(True)
                self.task.task_starter.cancel()

            # ... on évacue tous les lots en cours vers les sorties de secours...
            while self.task.active_carriers:
                self.task.active_carriers[0].abort(self.outlets)

            # ... on libère les opérateurs/ressources tenus par le poste...
            self.task.release()

            # ... et on reste en panne le temps de la réparation.
            self.task.is_in_breakdown.set(True)
            self.hold(self.mttr.sample())
            self.task.is_in_breakdown.set(False)


class ScheduledShutdown(sim.Component):
    """
    LES ARRÊTS PLANIFIÉS (arrêts PRÉVUS : maintenance programmée, pauses, nuit,
    week-end...). Contrairement aux pannes, ils sont connus à l'avance, sous
    forme d'intervalles de temps.

    Pendant un arrêt planifié, le poste ne démarre pas de nouveau lot. À la
    reprise, il pourra repartir.

    Paramètres :
      - task       : le poste concerné.
      - intervals  : la liste des fenêtres d'arrêt [début, fin]. Elles ne doivent
                     pas se chevaucher. Si None, le poste ne s'arrête jamais.
    """

    def setup(self, task: PieceTask, intervals: list[Interval] | None = None) -> None:
        self.task = task
        # On trie les arrêts dans l'ordre chronologique pour toujours connaître
        # le « prochain » arrêt à venir.
        self.intervals = sorted(intervals, key=lambda i: i.end) if intervals is not None else []

        for i in range(len(self.intervals)):
            for j in range(i + 1, len(self.intervals)):
                if not Interval.disjoint(self.intervals[i], self.intervals[j]):
                    raise ValueError("Scheduled shutdowns intervals must be pairwise disjoint")

        self.task.scheduled_shutdowns = self

    def next_shutdown(self) -> Interval | None:
        # Renvoie le prochain arrêt qui n'est pas encore terminé.
        for interval in self.intervals:
            if interval.end > env.now():
                return interval
        return None

    def can_resume_at(self, duration: float) -> float | None:
        # « Si je lance une opération qui dure `duration`, va-t-elle être coupée
        # par le prochain arrêt ? » Si oui, renvoie l'instant de reprise (la fin
        # de l'arrêt) ; sinon None (on a le temps).
        next_shutdown = self.next_shutdown()
        if next_shutdown is not None and env.now() + duration > next_shutdown.start:
            return next_shutdown.end
        return None

    def get_deadline(self) -> float:
        # L'« échéance » avant laquelle toute opération doit être terminée : c'est
        # le début du prochain arrêt planifié. S'il n'y en a pas, l'échéance est
        # l'infini (aucune contrainte).
        next_shutdown = self.next_shutdown()
        return next_shutdown.start if next_shutdown is not None else float('inf')

    def process(self):
        # Boucle de vie des arrêts : on attend le début de chaque arrêt, on coupe
        # le poste, on attend la fin, on autorise la reprise.
        while (next_shutdown := self.next_shutdown()) is not None:
            self.hold(till=next_shutdown.start)
            self.task.started_up = False
            self.task.is_in_shutdown.set(True)
            self.hold(till=next_shutdown.end)
            self.task.is_in_shutdown.set(False)
            self.task.is_frozen.set(False)


####################
# BATCH COLLECTORS #
####################

class BatchCollectorType(Enum):
    """
    LA STRATÉGIE DE CONSTITUTION DES LOTS : comment un poste choisit et regroupe
    les pièces avant de lancer une opération. Deux axes indépendants :

    1) DISCRIMINANT vs NON-DISCRIMINANT :
       - DISCRIMINANT  : un lot ne contient qu'UN SEUL modèle de pièce à la fois
                         (lot homogène). On choisit le modèle le plus présent et
                         on ne prend que celui-là. Utile quand chaque modèle a sa
                         propre durée d'opération (un four réglé pour un modèle).
       - NON-DISCRIMINANT : un lot peut MÉLANGER des modèles différents (du moment
                         qu'ils sont acceptés par le poste). Exige que tous les
                         modèles aient la même durée d'opération.

    2) GREEDY (gourmand) vs ALTRUISTE :
       - GREEDY    : on remplit le lot autant que possible avec ce qui est déjà
                     disponible, sans attendre.
       - ALTRUISTE : (variante prévue mais non encore implémentée ici).

    Seules les variantes GREEDY (gourmandes) sont disponibles actuellement.
    """

    DISCRIMINATING_GREEDY = auto()
    NON_DISCRIMINATING_GREEDY = auto()
    DISCRIMINATING_ALTRUISTIC = auto()
    NON_DISCRIMINATING_ALTRUISTIC = auto()

    @staticmethod
    def is_discriminating(bct: BatchCollectorType) -> bool:
        return bct in (BatchCollectorType.DISCRIMINATING_GREEDY, BatchCollectorType.DISCRIMINATING_ALTRUISTIC)


class PieceCollector(sim.Component):
    """
    LE « RAMASSEUR » DE PIÈCES d'un chariot (voir Carrier plus bas). Son rôle :
    aller chercher les pièces dans les stocks d'entrée du poste et constituer le
    lot qui sera traité. C'est lui qui réserve les « emplacements » de la machine
    (vacant_slots) au fur et à mesure qu'il prend des pièces.

    On ne le configure pas directement : il est choisi automatiquement selon la
    stratégie (BatchCollectorType) du poste.
    """

    def setup(self, task: PieceTask) -> None:
        self.task = task
        self.collected_pieces: list[Piece] = []        # les pièces déjà ramassées pour ce lot
        self.allow_dispatch = sim.State(value=False)
        self.done = sim.State(value=False)             # le lot est-il constitué ?


class NonDiscriminatingGreedyPieceCollector(PieceCollector):
    """
    RAMASSEUR NON-DISCRIMINANT & GOURMAND : prend n'importe quelle pièce acceptée,
    quel que soit son modèle, et remplit le lot au maximum de ce qui est
    disponible. Un lot peut donc mélanger plusieurs modèles.

    Déroulé :
      1) attendre le feu vert ;
      2) réserver le minimum d'emplacements et ramasser jusqu'au minimum requis
         (en attendant l'arrivée de pièces si besoin) ;
      3) tant qu'il reste de la place ET des pièces immédiatement disponibles,
         continuer à remplir jusqu'au maximum ;
      4) (si les chariots ne sont pas contigus) réserver tout de même la place
         restante pour occuper l'empreinte complète.
    """

    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        while len(self.collected_pieces) < self.task.config.min_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(
                self.collected_pieces) < self.task.config.max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take, fail_delay=0)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1))

            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        if not self.task.config.contiguous_carriers:
            remainder = self.task.config.max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder))

        self.done.set(True)
        self.passivate()


class DiscriminatingGreedyPieceCollector(PieceCollector):
    """
    RAMASSEUR DISCRIMINANT & GOURMAND : constitue un lot HOMOGÈNE (un seul modèle).
    Il choisit le modèle sur lequel se « concentrer » (le plus présent dans les
    stocks d'entrée, ou le premier à arriver si rien n'est disponible), puis ne
    ramasse que des pièces de ce modèle, en remplissant au maximum.

    Utile quand chaque modèle a sa propre durée / ses propres réglages : on évite
    de mélanger des pièces qui demanderaient des traitements différents.
    """

    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        present_models = [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]

        # Si aucune pièce valable n'est disponible, on attend la première et on se
        # concentre sur son modèle. Sinon on se concentre sur le modèle le plus présent.
        if not present_models:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take)
            assert isinstance(piece, Piece)
            focus_on = piece.model
        else:
            focus_on = Counter(present_models).most_common(1)[0][0]

        while len(self.collected_pieces) < self.task.config.min_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(
                self.collected_pieces) < self.task.config.max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on,
                                    fail_delay=0)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1))

            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        if not self.task.config.contiguous_carriers:
            remainder = self.task.config.max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder))

        self.done.set(True)
        self.passivate()


###################
# PIECE GENERATOR #
###################

class PieceGenerator(sim.Component, PickyPieceTaker):
    """
    LE GÉNÉRATEUR DE PIÈCES : la « source » de l'atelier. À intervalles réguliers
    (tirés au sort), il crée un paquet de pièces d'un modèle choisi au hasard
    selon des probabilités, et les dépose dans des stocks d'entrée. C'est ce qui
    alimente la ligne en matière à produire.

    Paramètres :
      - models_probs_batch_sizes : dictionnaire { modèle : (probabilité, taille) }.
            * probabilité : la chance que ce modèle soit choisi à chaque arrivée
                            (l'ensemble doit sommer à 1).
            * taille      : combien de pièces de ce modèle créer d'un coup.
      - duration : le temps ENTRE deux arrivées (tiré au sort). Plus il est court,
                   plus l'atelier est chargé en entrée.
      - outlets  : où déposer les pièces créées (généralement les stocks d'entrée
                   du premier poste).
    """

    def setup(self, models_probs_batch_sizes: dict[Model, tuple[float, int]], duration: sim.Distribution,
              outlets: list[Outlet]) -> None:
        self.models = list(models_probs_batch_sizes.keys())
        self.probs = list(v[0] for v in models_probs_batch_sizes.values())

        PickyPieceTaker.__init__(self, self.models)
        check_outlet_validity(self, outlets)
        check_probs(self.probs)

        self.batch_sizes = list(v[1] for v in models_probs_batch_sizes.values())
        self.duration = duration
        self.outlets = outlets

    def process(self):
        while True:
            self.hold(self.duration.sample())
            idx = np.random.choice(len(self.models), p=self.probs)
            for _ in range(self.batch_sizes[idx]):
                piece = Piece(model=self.models[idx])
                place([piece], self.outlets)


########
# TASK #
########

class Scope(Enum):
    """
    LA « PORTÉE » D'UNE CONSOMMATION : à quel rythme on consomme/mobilise une
    ressource ou des opérateurs. C'est une notion clé pour modéliser fidèlement
    les coûts.

      - PER_PIECE : proportionnel au NOMBRE DE PIÈCES du lot (ex. on consomme 1
                    dose de matière par pièce). Réservé aux ressources, pas aux
                    opérateurs.
      - PER_BATCH : une fois PAR LOT, quelle que soit sa taille (ex. l'opérateur
                    mobilisé le temps de lancer le lot, un réglage par fournée).
      - PER_TASK  : tenu pour TOUTE LA DURÉE DE VIE DU POSTE (ex. un opérateur
                    affecté en permanence à la machine, qui n'est libéré qu'en cas
                    de panne ou d'arrêt). Réservé aux opérateurs, pas aux ressources.
    """

    PER_PIECE = auto()
    PER_BATCH = auto()
    PER_TASK = auto()


@dataclass
class TaskConfig(ABC):
    """
    RÉGLAGES COMMUNS À TOUT POSTE. C'est ici que se trouvent les principaux
    « boutons » que vous pouvez tourner pour décrire votre atelier.

    --- Main d'œuvre et matière ---
      - operators            : opérateurs nécessaires pour FAIRE TOURNER le poste,
                               sous forme de liste (ressource, quantité). Ex. :
                               [(equipe, 2)] = il faut 2 personnes de l'équipe.
      - operators_scope      : à quel rythme on mobilise ces opérateurs
                               (PER_BATCH ou PER_TASK ; PER_PIECE interdit).
      - resources_scope      : à quel rythme on consomme les matières/ressources
                               (PER_PIECE ou PER_BATCH ; PER_TASK interdit).

    --- Démarrage de la machine ---
      - startup_operators    : opérateurs nécessaires pour DÉMARRER le poste
                               (mise en route), liste (ressource, quantité).
      - startup_duration     : durée de la mise en route (tirée au sort).

    --- Lots et chariots (voir l'explication détaillée dans la classe Carrier) ---
      - min_carriers         : nombre MINIMUM de lots prêts avant d'autoriser leur
                               lancement. Sert à synchroniser : on attend d'avoir
                               assez de lots prêts avant de démarrer.
      - min_carrier_capacity : nombre MINIMUM de pièces dans un lot pour pouvoir
                               le lancer (ex. un four qu'on ne lance pas à moitié
                               vide).
      - max_carrier_capacity : nombre MAXIMUM de pièces dans un seul lot (la taille
                               d'un support / d'une fournée).
      - max_capacity         : nombre TOTAL d'emplacements de la machine, partagés
                               entre tous les lots présents en même temps (la
                               capacité physique du poste).
      - contiguous_carriers  : True = chaque lot occupe une « empreinte » complète
                               et réservée (les emplacements non remplis restent
                               bloqués) ; False = les lots peuvent se serrer et
                               partager l'espace plus librement.
      - independent_carriers : True = les lots avancent chacun à leur rythme, sans
                               s'attendre ; False = les lots lancés ensemble
                               attendent que tous soient finis avant que le poste
                               ne reparte (fonctionnement synchronisé).
    """

    operators: list[tuple[sim.Resource, int]]
    operators_scope: Scope
    resources_scope: Scope
    startup_operators: list[tuple[sim.Resource, int]]
    startup_duration: sim.Distribution

    min_carriers: int
    min_carrier_capacity: int
    max_carrier_capacity: int
    max_capacity: int
    contiguous_carriers: bool
    independent_carriers: bool


class TaskStarter(sim.Component):
    """
    LA MISE EN ROUTE D'UN POSTE. Avant de pouvoir produire, une machine doit
    démarrer : cela prend du temps (startup_duration) et peut nécessiter des
    opérateurs (startup_operators).

    Particularité réaliste : si un arrêt planifié est imminent et que la mise en
    route n'aurait pas le temps de finir avant, on attend la fin de l'arrêt pour
    démarrer (inutile de lancer un démarrage qui serait aussitôt coupé). Si l'on
    n'arrive pas à mobiliser les opérateurs de démarrage à temps, le poste reste
    « gelé » jusqu'au prochain redémarrage possible.
    """

    def setup(self, task: PieceTask) -> None:
        self.task = task
        self.done = sim.State(value=False)

    def process(self):
        duration = self.task.config.startup_duration.sample()
        while (resume_at := self.task.scheduled_shutdowns.can_resume_at(duration)) is not None:
            self.hold(till=resume_at)

        deadline = self.task.scheduled_shutdowns.get_deadline()
        self.request(*self.task.config.startup_operators, fail_at=deadline - duration, cap_now=True)
        if self.failed():
            self.task.is_frozen.set(True)
            self.done.set(True)
            return

        self.hold(duration)
        self.task.started_up = True
        self.done.set(True)
        # Les opérateurs de démarrage sont libérés automatiquement.


class Carrier(sim.Component, ABC):
    """
    ================== LE CHARIOT : LA NOTION CENTRALE À COMPRENDRE ==================

    Un CHARIOT (« Carrier ») représente UNE FOURNÉE / UN PASSAGE DE LA MACHINE :
    c'est le support qui rassemble un groupe de pièces (un LOT), occupe les
    emplacements de la machine pendant l'opération, mobilise les opérateurs et la
    matière nécessaires, fait tourner l'opération pendant sa durée, puis dépose
    les pièces finies en sortie.

    Pensez-y comme un plateau / une palette / une grille de four : on le charge de
    pièces, on le passe dans la machine, on le décharge. Selon le réglage du poste,
    plusieurs chariots peuvent exister en même temps (plusieurs fournées en
    parallèle) en se partageant la capacité totale de la machine.

    CYCLE DE VIE D'UN CHARIOT :
      1) RAMASSAGE : un « ramasseur » (PieceCollector) va chercher des pièces dans
         les stocks d'entrée et les charge sur le chariot, en réservant au passage
         les emplacements correspondants. Le chariot est « chargé » (loaded) quand
         le lot est constitué.
      2) ATTENTE DU FEU VERT : le poste décide quand lancer (selon min_carriers et
         le mode synchronisé ou non).
      3) MOBILISATION : on réserve les opérateurs et la matière requis (selon les
         « portées » PER_PIECE / PER_BATCH), en respectant l'échéance avant le
         prochain arrêt planifié.
      4) OPÉRATION : la machine travaille pendant la durée de l'opération.
      5) DÉPÔT : les pièces finies sont déposées dans les sorties du poste, les
         emplacements et les opérateurs sont libérés.

    INTERRUPTIONS :
      - Si une PANNE survient, ou si le temps manque avant un ARRÊT PLANIFIÉ, le
        chariot est « avorté » (abort) : ses pièces sont évacuées (vers les sorties
        de secours en cas de panne, ou remises en entrée si on manque de temps),
        et les emplacements/opérateurs sont rendus. Aucune pièce n'est perdue ni
        dupliquée.

    Cette classe est abstraite : la version concrète pour des pièces est
    PieceCarrier (plus bas).
    """

    @abstractmethod
    def setup(self, task: Task) -> None:
        self.task = task
        self.allow_dispatch = sim.State(value=False)   # feu vert pour lancer l'opération
        self.loaded = sim.State(value=False)           # le lot est-il chargé ?
        self.done = sim.State(value=False)             # le chariot a-t-il terminé ?

    @abstractmethod
    def abort(self, *args) -> None:
        # Avorter proprement le chariot (panne ou manque de temps) : évacuer les
        # pièces et libérer ce qui était tenu.
        pass

    @abstractmethod
    def freeze_abort_if(self, condition, *args) -> None:
        # Si la condition est vraie (plus assez de temps avant l'arrêt), « geler »
        # le poste et avorter le chariot.
        pass


class Task(sim.Component, ABC):
    """
    UN POSTE DE TRAVAIL (ou machine) — le cœur de l'atelier. C'est lui qui,
    en boucle, démarre, prépare un lot (via un chariot), le lance, puis recommence.

    Il orchestre tout : démarrage, réapprovisionnement, création des chariots,
    synchronisation des lots, et il réagit aux pannes et aux arrêts planifiés.

    Trois « états » résument sa situation :
      - is_in_breakdown : le poste est en panne (arrêt subi).
      - is_in_shutdown  : le poste est en arrêt planifié.
      - is_frozen       : le poste est « gelé » — une opération n'a pas pu finir à
                          temps avant un arrêt, on attend la reprise pour réessayer.

    Classe abstraite : la version concrète pour des pièces est PieceTask (plus bas).
    Paramètres : voir TaskConfig (et PieceTaskConfig) pour tous les réglages.
    """

    @abstractmethod
    def setup(self, config: TaskConfig, carrier_type: type[Carrier]) -> None:
        # Garde-fous : certaines portées n'ont pas de sens.
        if config.operators_scope is Scope.PER_PIECE:
            raise ValueError("Operators scope cannot be PER_PIECE")
        if config.resources_scope is Scope.PER_TASK:
            raise ValueError("Resources scope cannot be PER_TASK")

        self.config = config
        self.carrier_type = carrier_type

        # vacant_slots = les EMPLACEMENTS PHYSIQUES de la machine, partagés entre
        # tous les chariots (lots) présents. Sa capacité = max_capacity.
        self.vacant_slots = sim.Resource(capacity=config.max_capacity)
        self.started_up = False
        self.task_starter = None
        self.is_in_breakdown = sim.State(value=False)
        self.is_in_shutdown = sim.State(value=False)
        # is_frozen : phase d'avant-arrêt où une opération a été avortée faute de
        # temps ; on attend la fin de l'arrêt pour retenter (démarrage ou lancement
        # de chariots).
        self.is_frozen = sim.State(value=False)
        self.scheduled_shutdowns = ScheduledShutdown(task=self)

        self.active_carriers: list[Carrier] = []    # les chariots/lots actuellement en cours

    @abstractmethod
    def handle_restock(self) -> None:
        # Déclenche les réapprovisionnements le cas échéant (dépend du type de poste).
        pass

    def process(self):
        while True:
            # On ne fait rien tant que le poste est en panne, en arrêt planifié, ou gelé.
            self.wait((self.is_in_breakdown, False), (self.is_in_shutdown, False), (self.is_frozen, False), all=True)

            # --- Mise en route si le poste n'est pas déjà démarré ---
            if not self.started_up:
                self.task_starter = TaskStarter(task=self)
                self.wait(self.task_starter.done)

                # Le démarrage a-t-il échoué (opérateurs indisponibles à temps) ?
                if not self.started_up:
                    # SANS is_frozen, on retenterait aussitôt de démarrer, et le
                    # redémarrage pourrait réussir si l'on tirait au sort une durée
                    # de démarrage plus courte que la précédente — comportement
                    # irréaliste qu'on veut éviter.
                    continue

                # Opérateurs tenus en permanence (PER_TASK) : on les réserve dès le
                # démarrage et on les garde.
                if self.config.operators_scope is Scope.PER_TASK:
                    deadline = self.scheduled_shutdowns.get_deadline()
                    self.request(*self.config.operators, fail_at=deadline)

                    # Pas assez d'opérateurs à temps ?
                    if self.failed():
                        # Mettre is_frozen ici est redondant (un échec sur l'échéance
                        # vient d'un arrêt planifié, donc is_in_shutdown serait déjà
                        # vrai), mais on le note pour la clarté.
                        self.is_frozen.set(True)
                        continue

            # --- Réapprovisionnement éventuel ---
            self.handle_restock()

            # --- Création d'un nouveau chariot (un nouveau lot) ---
            new_carrier = self.carrier_type(task=self)
            self.active_carriers.append(new_carrier)
            self.wait(new_carrier.loaded)    # on attend que le lot soit constitué

            # --- Lancement synchronisé éventuel ---
            # Si l'un des chariots a été avorté par un arrêt planifié, tous les
            # chariots non lancés le sont aussi.
            non_dispatched_carriers = [carrier for carrier in self.active_carriers if not carrier.allow_dispatch.get()]
            if len(non_dispatched_carriers) >= self.config.min_carriers:
                for carrier in non_dispatched_carriers:
                    carrier.allow_dispatch.set(True)

                # En mode non-indépendant, le poste attend que tous les lots lancés
                # ensemble soient terminés avant de repartir.
                if not self.config.independent_carriers:
                    self.wait(*[carrier.done for carrier in non_dispatched_carriers], all=True)


##############
# PIECE TASK #
##############

@dataclass
class PieceTaskConfig(TaskConfig):
    """
    RÉGLAGES SPÉCIFIQUES À UN POSTE QUI TRAITE DES PIÈCES (en plus des réglages
    communs de TaskConfig).

    Paramètres :
      - models_durations      : dictionnaire { modèle : durée d'opération }. C'est
                                la « capacité » du poste : les modèles qu'il sait
                                traiter, et le temps que prend l'opération pour
                                chacun. En mode NON-DISCRIMINANT, toutes les durées
                                doivent être identiques (un lot mélangé ne peut pas
                                avoir deux durées).
      - resources             : matières/ressources consommées, liste
                                (ressource, quantité). La quantité s'interprète
                                selon resources_scope (par pièce ou par lot).
      - batch_collector_type  : la stratégie de constitution des lots
                                (voir BatchCollectorType).
    """

    models_durations: dict[Model, sim.Distribution]  # capacité du poste
    resources: list[tuple[sim.Resource, float]]
    batch_collector_type: BatchCollectorType


class PieceCarrier(Carrier):
    """
    LE CHARIOT CONCRET POUR DES PIÈCES. Met en œuvre le cycle de vie décrit dans
    la classe Carrier : ramassage des pièces (via le ramasseur correspondant à la
    stratégie du poste), réservation de la matière et des opérateurs, opération,
    puis dépôt en sortie. Gère aussi l'évacuation propre en cas de panne ou de
    manque de temps.
    """

    @override
    def setup(self, task: PieceTask) -> None:
        super().setup(task=task)

        # On choisit le ramasseur selon la stratégie du poste.
        bct = task.config.batch_collector_type
        if bct is BatchCollectorType.DISCRIMINATING_GREEDY:
            self.batch_collector = DiscriminatingGreedyPieceCollector(task=task)
        elif bct is BatchCollectorType.NON_DISCRIMINATING_GREEDY:
            self.batch_collector = NonDiscriminatingGreedyPieceCollector(task=task)

    @override
    def abort(self, *args) -> None:
        # AVORTER le chariot : on évacue les pièces déjà ramassées vers les sorties
        # fournies (sorties de secours en cas de panne, ou stocks d'entrée si l'on
        # manque de temps), puis on libère emplacements et opérateurs.
        lifeboats = args[0]
        place(self.batch_collector.collected_pieces, lifeboats)

        self.batch_collector.done.set(True)
        self.loaded.set(True)
        self.batch_collector.cancel()

        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)

        self.done.set(True)
        self.cancel()

    @override
    def freeze_abort_if(self, condition: bool) -> None:
        # Si la condition est vraie (plus assez de temps avant l'arrêt planifié),
        # on gèle le poste et on avorte le chariot en remettant les pièces en entrée.
        if condition:
            self.task.is_frozen.set(True)
            self.abort(self.task.inlets)

    def process(self):
        # L'échéance : tout doit être fini avant le prochain arrêt planifié.
        deadline = self.task.scheduled_shutdowns.get_deadline()

        # 1) RAMASSAGE : on lance le ramasseur et on attend que le lot soit prêt.
        self.batch_collector.allow_dispatch.set(True)
        # On ne connaît pas encore la durée de l'opération : on se contente de
        # viser l'échéance (et pas échéance - durée).
        self.wait(self.batch_collector.done, fail_at=deadline)
        self.freeze_abort_if(self.failed())
        self.loaded.set(True)

        # La durée de l'opération dépend du modèle des pièces du lot.
        model = self.batch_collector.collected_pieces[0].model
        duration = self.task.config.models_durations[model].sample()

        resources_to_request = []

        # 2) MOBILISATION DES OPÉRATEURS (par lot) + réappro des consommables.
        if self.task.config.operators_scope is Scope.PER_BATCH:
            resources_to_request.extend(self.task.config.operators)
            for resource, _ in self.task.config.resources:
                if isinstance(resource, RestockableResource):
                    resource.restock(demander=self)

        # 3) MATIÈRE : par lot (quantité fixe) ou par pièce (quantité × nb de pièces).
        if self.task.config.resources_scope is Scope.PER_BATCH:
            resources_to_request.extend(self.task.config.resources)
        elif self.task.config.resources_scope is Scope.PER_PIECE:
            resources_to_request.extend(
                [(resource, quantity * len(self.batch_collector.collected_pieces)) for resource, quantity in
                 self.task.config.resources])

        # On vérifie qu'il reste assez de temps avant de réserver la matière.
        self.freeze_abort_if(env.now() > deadline - duration)
        self.request(*resources_to_request, fail_at=deadline - duration)
        # A-t-on manqué de matière/opérateurs à temps ?
        self.freeze_abort_if(self.failed())

        # 4) ATTENTE DU FEU VERT DE LANCEMENT (toujours dans les temps).
        self.freeze_abort_if(env.now() > deadline - duration)
        self.wait(self.allow_dispatch, fail_at=deadline - duration)
        # N'a-t-on pas pu lancer le chariot à temps ?
        self.freeze_abort_if(self.failed())

        # 5) OPÉRATION : la machine travaille pendant `duration`.
        assert env.now() + duration <= deadline, "Failed to dispatch carrier despite there being enough time"
        self.hold(duration)

        # 6) DÉPÔT des pièces finies en sortie, puis libération des emplacements.
        place(self.batch_collector.collected_pieces, self.task.outlets)
        self.batch_collector.cancel()    # libère les emplacements (un seul propriétaire)

        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)

        self.done.set(True)
        # Opérateurs et emplacements sont libérés automatiquement.


class PieceTask(Task, PickyPieceTaker):
    """
    UN POSTE QUI TRAITE DES PIÈCES (le cas usuel). Il a des stocks d'entrée
    (inlets) où il prend les pièces, et des sorties (outlets) où il dépose les
    pièces finies.

    Paramètres :
      - config   : les réglages (un PieceTaskConfig).
      - inlets   : les stocks d'entrée d'où le poste tire les pièces.
      - outlets  : les destinations de sortie des pièces finies.
    """

    @override
    def setup(self, config: PieceTaskConfig, inlets: list[Buffer], outlets: list[Outlet]) -> None:
        super().setup(config=config, carrier_type=PieceCarrier)

        PickyPieceTaker.__init__(self, list(config.models_durations.keys()))
        check_inlet_validity(self, inlets)
        check_outlet_validity(self, outlets)

        self.inlets = inlets
        self.outlets = outlets

    @override
    def handle_restock(self) -> None:
        # Pour les opérateurs tenus en permanence (PER_TASK), c'est le poste
        # lui-même qui déclenche le réapprovisionnement à chaque tour.
        if self.config.operators_scope is Scope.PER_TASK:
            for resource, _ in self.config.resources:
                if isinstance(resource, RestockableResource):
                    resource.restock(demander=self)


'''
TODO : AJOUTER UN RAMASSEUR DE RESSOURCES (variantes gourmande et altruiste)

#################
# RESOURCE TASK #
#################

Un « poste à ressources » (par opposition à un poste à pièces) transformerait
des QUANTITÉS de matière plutôt que des pièces individuelles dénombrables. Pour
que les notions min_carrier_capacity / max_carrier_capacity y aient un sens, il
faudrait créer un ramasseur de ressources dédié (avec ses variantes gourmande et
altruiste, comme pour les pièces), qui vérifie les emplacements disponibles, les
réserve et demande les ressources. Ce travail reste à faire — d'où la mise en
commentaire de cette section.

@dataclass
class ResourceTaskConfig(TaskConfig):
    resources_in_salvageable: list[tuple[sim.Resource, float, bool]]
    resources_out_distr: list[tuple[sim.Resource, sim.Bounded]]
    primary_resource: sim.Resource
    task_duration: sim.Distribution


class ResourceCarrier(Carrier):
    @override
    def setup(self, task: ResourceTask) -> None:
        super().setup(task=task)
        self.requested_resources: list[tuple[sim.Resource, float, bool]] = []

    @override
    def abort(self, *args) -> None:
        for resource, quantity, salvageable in self.requested_resources:
            if salvageable:
                self.request((resource, -quantity))
        self.loaded.set(True)
        self.done.set(True)
        self.cancel()

    @override
    def freeze_abort_if(self, condition: bool) -> None:
        if condition:
            self.task.is_frozen.set(True)
            self.abort()

    @override
    def process(self):
        duration = self.task.config.task_duration.sample()
        deadline = self.task.scheduled_shutdowns.get_deadline()

        self.freeze_abort_if(env.now() > deadline - duration)

        resources_to_request = []

        if self.task.config.operators_scope is Scope.PER_BATCH:
            resources_to_request.extend(self.task.config.operators)
            for resource, _, _ in self.task.config.resources_in_salvageable:
                if isinstance(resource, RestockableResource):
                    resource.restock(demander=self)

        if self.task.config.resources_scope is Scope.PER_BATCH:
            self.request(*[(r, q) for r, q, _ in self.task.config.resources_in_salvageable], fail_at=deadline - duration)
            self.freeze_abort_if(self.failed())



        assert env.now() <= deadline - duration, "Failed to attempt requesting resources despite there still being time"
        self.request(*resources_to_request, fail_at=deadline - duration)
        self.freeze_abort_if(self.failed())
        self.loaded.set(True)

        assert env.now() <= deadline - duration, "Failed to attempt waiting for dispatch despite being time"
        self.wait(self.allow_dispatch, fail_at=deadline - duration)

        self.hold(self.task.config.task_duration.sample())

        for resource, distr in self.task.config.resources_out:
            self.request((resource, -distr.sample()))

        self.done.set(True)


class ResourceTask(Task):
    @override
    def setup(self, config: ResourceTaskConfig):
        if any(distr.lowerbound < 0 or distr.upperbound == float('inf') for _, distr in config.resources_out_distr):
            raise ValueError("Output resource distribution must be bounded in [0, +inf[")

        super().setup(config=config, carrier_type=ResourceCarrier)

    @override
    def handle_restock(self) -> None:
        if self.config.operators_scope is Scope.PER_TASK:
            for resource, _, _ in self.config.resources_in_salvageable:
                if isinstance(resource, RestockableResource):
                    resource.restock(demander=self)
'''
