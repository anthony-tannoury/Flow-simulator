"""
Moteur de simulation d'atelier (simulation à événements discrets, via salabim).

------------------------------------------------------------------------------
1. L'IDÉE GÉNÉRALE
------------------------------------------------------------------------------
Ce fichier simule un atelier de production. Concrètement :
  - des PIÈCES sont créées par une source,
  - elles patientent dans des STOCKS (les buffers),
  - des POSTES DE TRAVAIL (les tâches) les prennent, les transforment pendant une
    certaine durée, puis les reposent dans d'autres stocks,
  - et ainsi de suite jusqu'à la sortie.

Le temps n'avance pas en continu : il « saute » d'un événement au suivant (une
pièce créée, une tâche terminée, une panne qui démarre...). C'est ce qui permet de
simuler des heures ou des jours de production en quelques secondes.

Le flux que vous dessinez est donc un réseau :

    Source ──► Stock ──► Poste ──► Stock ──► Poste ──► ... ──► Stock de sortie
  (FirstTask)  (Buffer)  (Task)   (Buffer)  (Task)             (Buffer)

Tout le reste (lois de durée, opérateurs, consommables, pannes, arrêts, mesures)
vient SE BRANCHER sur ces éléments pour les paramétrer. Chaque concept est expliqué
en commentaire juste au-dessus du code correspondant, plus bas dans ce fichier.

------------------------------------------------------------------------------
2. LE CYCLE DE VIE D'UNE PIÈCE
------------------------------------------------------------------------------
  1. Naissance      : la source (FirstTask) crée la pièce d'un certain modèle et la
                      dépose dans un stock de sortie (directement ou via un SoftBuffer
                      qui l'aiguille).
  2. Attente        : la pièce patiente dans un HardBuffer parmi celles qu'il accepte.
  3. Prise en charge: un poste (Task) dont la capability couvre le modèle la pioche
                      — éventuellement avec d'autres pour former un LOT — dès qu'il a
                      les opérateurs/consommables nécessaires et qu'il n'est ni en
                      panne ni à l'arrêt.
  4. Transformation : le poste traite le lot pendant sa durée de traitement.
  5. Sortie         : les pièces finies vont dans le stock de sortie correspondant à
                      leur modèle (voir la « règle de la partition » au-dessus de Task).
  6. Répétition     : les étapes 2 à 5 se répètent de poste en poste.
  7. Fin de parcours: la pièce finit dans un stock « Exit », ou au « Scrap » si elle a
                      été évacuée lors d'une panne.

------------------------------------------------------------------------------
3. MÉMO DES RÈGLES À RESPECTER (vérifiées au démarrage, sinon le modèle refuse de
   se construire)
------------------------------------------------------------------------------
  - SoftBuffer : toutes les destinations acceptent les mêmes modèles ; probabilités
    dans [0, 1] et de somme 1.
  - Sorties de Task / FirstTask : elles forment une PARTITION de la capability
    (disjointes ET couvrant tous les modèles produits).
  - FirstTask : probabilités des modèles dans [0, 1] et de somme 1.
  - Opérateurs : portée PER_BATCH ou PER_TASK (jamais PER_PIECE).
  - Consommables : portée PER_PIECE ou PER_BATCH (jamais PER_TASK).
  - Interval : début <= fin ; ScheduledShutdowns : intervalles disjoints.

Document complet (non technique) destiné aux concepteurs de flux :
voir documentation_simulation_fr.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
import salabim as sim

# Les durées du modèle (création, traitement, démarrage, MTBF/MTTR, livraison...)
# sont pilotées par des LOIS DE PROBABILITÉ salabim (sim.Constant, sim.Normal,
# sim.Triangular, sim.Exponential) plutôt que par des valeurs fixes, afin de
# refléter la variabilité réelle. C'est avec ces lois qu'on règle le rythme et
# l'incertitude de l'atelier.
sim.yieldless(True)
env = sim.Environment(random_seed=42, trace=False)


#########
# UTILS #
#########

class Interval:
    def __init__(self, start: float, end: float) -> None:
        if start > end:
            raise ValueError("Interval start must be before its end")
        self.start = start
        self.end = end

    @property
    def length(self):
        return self.end - self.start

    @staticmethod
    def disjoint(int1: Interval, int2: Interval) -> bool:
        if int1.start > int2.start:
            int1, int2 = int2, int1
        return int1.end < int2.start



##########
# MODELS #
##########
#
# LES MODÈLES DE PIÈCES
# ---------------------
# Chaque pièce appartient à UN modèle. Les modèles forment une hiérarchie
# parent / enfant, comme une famille de produits :
#
#         M1                 M2
#        /  \                |
#      C1    C2             C3
#
# RÈGLE CLÉ — l'héritage descendant : quand un stock ou une tâche « accepte » un
# modèle, il accepte CE modèle ET TOUS SES DESCENDANTS. Un stock qui accepte M1
# accepte donc aussi C1 et C2 ; un stock qui accepte seulement C1 n'accepte ni M1
# ni C2. Voyez les modèles parents comme des CATÉGORIES : choisir une catégorie
# large accepte toute sa descendance ; choisir une feuille précise ne prend que
# celle-là. C'est ce mécanisme qui trie automatiquement les pièces dans le bon
# stock et les fait prendre par le bon poste (voir PickyPieceTaker.can_take).

class Model:
    def __init__(self, name: str, parent: Model | None = None) -> None:
        self.name = name
        self.parent = parent

    def __repr__(self) -> str:
        if self.parent is not None:
            return f"{ {self.name}, {self.parent.name} }"
        return f"{ {self.name} }"


#########
# PIECE #
#########
#
# LES PIÈCES
# ----------
# Une pièce, c'est simplement UN MODÈLE + un identifiant unique (et l'instant de sa
# création, utilisé pour mesurer son temps de traversée). Les pièces ne « décident »
# de rien : elles se laissent transporter par le flux. Ce sont les stocks et les
# tâches qui décident qui prend quoi.
#
# PickyPieceTaker est la brique commune (stocks, tâches) qui sait, pour un modèle
# donné, s'il est accepté : can_take remonte la chaîne des parents jusqu'à trouver
# un modèle accepté (c'est l'héritage descendant décrit plus haut).

class Piece(sim.Component):
    ID = 0

    def setup(self, model: Model) -> None:
        self.model = model
        self.id = str(Piece.ID).zfill(6)
        Piece.ID += 1


class PickyPieceTaker:
    def __init__(self, valid_models: list[Model]) -> None:
        self.valid_models = valid_models
        # Memoize the capability walk: each distinct resolved model is classified once.
        self._take_cache: dict[Model, bool] = {}

    def can_take(self, obj: Piece | Model) -> bool:
        model = obj.model if isinstance(obj, Piece) else obj
        cached = self._take_cache.get(model)
        if cached is not None:
            return cached
        can_take_piece = False
        m = model
        while m is not None and not can_take_piece:
            can_take_piece |= m in self.valid_models
            m = m.parent
        self._take_cache[model] = can_take_piece
        return can_take_piece

    def can_flush_into(self, other: PickyPieceTaker):
        for model in self.valid_models:
            if not other.can_take(model):
                return False
        return True

    @staticmethod
    def disjoint(ppt1: PickyPieceTaker, ppt2: PickyPieceTaker) -> bool:
        for model in ppt1.valid_models:
            if ppt2.can_take(model):
                return False

        for model in ppt2.valid_models:
            if ppt1.can_take(model):
                return False

        return True

    @staticmethod
    def same_valid_models(ppt1: PickyPieceTaker, ppt2: PickyPieceTaker) -> bool:
        return ppt1.can_flush_into(ppt2) and ppt2.can_flush_into(ppt1)


def _note_buffer_arrival(buffer, piece: Piece) -> None:
    """Appelé à chaque fois qu'une pièce entre dans un stock : met à jour les
    mesures d'arrivée (temps de traversée) et réveille les collecteurs altruistes
    en attente sur ce stock."""
    monitors = getattr(buffer, "arrival_monitors", None)
    if monitors:
        delay = env.now() - piece.creation_time()
        for mon in monitors:
            mon.tally(delay)

    signal = getattr(buffer, "arrival_signal", None)
    if signal is not None:
        signal.set(signal.value() + 1)


class PiecePlacer(sim.Component):
    def setup(self, pieces: list[Piece], bufs_out: list[Buffer]):
        self.pieces = pieces
        self.bufs_out = bufs_out
        self.done = sim.State(value=False)

    def process(self):
        for piece in self.pieces:
            for buf_out in self.bufs_out:
                if buf_out.can_take(piece):
                    target = buf_out.choose_buffer() if isinstance(buf_out, SoftBuffer) else buf_out
                    self.to_store(target, piece)
                    _note_buffer_arrival(target, piece)
                    break

        self.done.set(True)


###########
# BUFFERS #
###########
#
# LES STOCKS (buffers)
# --------------------
# Il existe DEUX natures de stock très différentes. Bien les distinguer est
# essentiel au moment de la conception.

class Buffer(PickyPieceTaker):
    def __init__(self, valid_models: list[Model]) -> None:
        super().__init__(valid_models)


# LE STOCK RÉEL — HardBuffer
# Une file d'attente concrète : les pièces y patientent réellement. Il a une liste
# de modèles acceptés (avec l'héritage du §MODELS). Une tâche vient y piocher les
# pièces dont elle a besoin. C'est le seul endroit où les pièces « existent » entre
# deux postes, et le seul type de stock que l'on peut observer avec un Monitor.
class HardBuffer(sim.Store, Buffer):
    def setup(self, valid_models: list[Model]) -> None:
        PickyPieceTaker.__init__(self, valid_models)
        self.arrival_monitors: list[sim.Monitor] = []
        self.arrival_signal = None


# L'AIGUILLAGE PROBABILISTE — SoftBuffer
# N'est PAS un vrai stock : c'est un ROUTEUR. Il ne garde aucune pièce ; il décide
# vers quel stock réel envoyer chaque pièce, selon des probabilités (ex. : 70 % vers
# le stock A, 30 % vers le stock B). À utiliser pour un partage de flux aléatoire
# (ex. un contrôle qualité qui envoie un pourcentage en retouche).
# Règles : toutes les destinations doivent accepter EXACTEMENT les mêmes modèles ;
# chaque probabilité dans [0, 1] ; la somme des probabilités vaut 1.
class SoftBuffer(Buffer):
    def __init__(self) -> None:
        self.bufs_out = None
        self.probs = None

    def choose_buffer(self) -> Buffer:
        rand = sim.Uniform(0, 1).sample()
        cursor = 0

        for i in range(len(self.bufs_out)):
            cursor += self.probs[i]
            if rand < cursor:
                return self.bufs_out[i]
        return self.bufs_out[-1]

    def init(self, bufs_out_probs: list[tuple[Buffer, float]]) -> None:
        if not all(PickyPieceTaker.same_valid_models(bufs_out_probs[0][0], buf_out) for buf_out, _ in bufs_out_probs):
            raise ValueError("All buffers in soft buffer must accept the same models")

        if not all(0 <= prob <= 1 for _, prob in bufs_out_probs):
            raise ValueError("Probabilities in soft buffer must be in [0, 1]")

        if not abs(sum(prob for _, prob in bufs_out_probs) - 1) < 1e-9:
            raise ValueError("Probabilities in soft buffer must sum to 1")

        super().__init__(bufs_out_probs[0][0].valid_models)

        self.bufs_out = [buf_out for buf_out, _ in bufs_out_probs]
        self.probs = [prob for _, prob in bufs_out_probs]


###################
# BATCH COLLECTOR #
###################
#
# LE TRAITEMENT PAR LOTS (batch)
# ------------------------------
# Une tâche ne traite pas forcément les pièces une par une : elle constitue un LOT.
#   - min_capacity : il faut AU MOINS ce nombre de pièces pour démarrer.
#   - max_capacity : le lot (et le nombre de pièces simultanément « en cours ») ne
#     peut pas dépasser cette taille. Régler min = max = 1 => traitement pièce par
#     pièce classique.
#
# Deux stratégies de constitution du lot (batch_collector) :
#   - GreedyBatchCollector (gourmand) : dès que min_capacity pièces sont réunies, le
#     lot démarre ; s'il reste des pièces disponibles tout de suite, il en prend
#     autant que possible jusqu'à max_capacity. Réactif.
#   - AltruisticBatchCollector (altruiste) : ne saisit des pièces QUE si un lot
#     complet d'au moins min_capacity peut être formé d'un seul coup ; sinon il ne
#     prend rien et laisse les pièces disponibles pour d'autres postes. Évite de
#     garder des pièces « en otage ».
# Choisir gourmand pour la vitesse ; altruiste quand plusieurs postes se partagent
# les mêmes stocks et qu'on ne veut pas qu'un poste accapare des pièces.

class BatchCollector(sim.Component):
    def setup(self, task: Task) -> None:
        self.task = task
        self.collected_pieces = []
        self.reserved_slots = 0
        self.done = sim.State(value=False)

    def reserve_slot(self):
        self.request((self.task.vacant_slots, 1))
        self.reserved_slots += 1

    def release_reserved_slots(self):
        if self.reserved_slots <= 0:
            return
        self.release()
        self.reserved_slots = 0
        self.task.notify_slot_freed()

    def update_done(self):
        self.done.set(len(self.collected_pieces) >= self.task.config.min_capacity)


class GreedyBatchCollector(BatchCollector):
    def process(self):
        task = self.task
        min_cap = task.config.min_capacity

        # Phase 1 : atteindre min_capacity (on attend les pièces et les places libres).
        while len(self.collected_pieces) < min_cap:
            self.reserve_slot()
            piece = self.from_store(task.bufs_in, filter=task.can_take)
            self.collected_pieces.append(piece)

        # Phase 2 : absorber gloutonnement les pièces déjà disponibles, dans la limite
        # de la capacité restante.
        while task.vacant_slots.available_quantity() > 0:
            piece = self.from_store(task.bufs_in, filter=task.can_take, fail_delay=0)
            if piece is None:
                break
            self.reserve_slot()
            self.collected_pieces.append(piece)

        self.update_done()


class AltruisticBatchCollector(BatchCollector):
    """Collecteur altruiste : ne prend des pièces que lorsqu'un lot complet d'au
    moins min_capacity peut être formé d'un seul coup (les pièces ne sont jamais
    retenues en otage en attendant)."""

    def process(self):
        task = self.task
        min_cap = task.config.min_capacity

        for buf_in in task.bufs_in:
            if buf_in.arrival_signal is None:
                buf_in.arrival_signal = sim.State("arrival." + buf_in.name(), value=0)
        if task.slot_signal is None:
            task.slot_signal = sim.State("slot." + task.name(), value=0)

        while True:
            capacity = int(task.vacant_slots.available_quantity())
            valid_pieces = []
            if capacity > 0:
                for buf_in in task.bufs_in:
                    for piece in buf_in:
                        if task.can_take(piece):
                            valid_pieces.append((piece, buf_in))
                            if len(valid_pieces) >= capacity:
                                break
                    if len(valid_pieces) >= capacity:
                        break

            if len(valid_pieces) >= min_cap:
                for piece, buf_in in valid_pieces:
                    self.from_store(buf_in, filter=lambda p, q=piece: p is q)
                    self.collected_pieces.append(piece)
                self.request((task.vacant_slots, len(valid_pieces)))
                self.update_done()
                return

            # Pas encore de quoi former un lot : on attend qu'une entrée change
            # (arrivée d'une pièce ou libération d'une place).
            snapshot = [(buf_in.arrival_signal, buf_in.arrival_signal.value()) for buf_in in task.bufs_in]
            snapshot.append((task.slot_signal, task.slot_signal.value()))
            self.wait(*[(state, (lambda v, c, s, base=base: v != base)) for state, base in snapshot])


########
# TASK #
########
#
# LES POSTES DE TRAVAIL — Task
# ----------------------------
# C'est le cœur du modèle : un poste PREND des pièces dans ses stocks d'entrée
# (bufs_in), les TRAITE pendant une durée, puis les DÉPOSE dans ses stocks de sortie
# (bufs_out).
#
# CAPABILITY : la liste des modèles que la tâche sait traiter (héritage inclus). Elle
#   ne piochera jamais une pièce qu'elle ne sait pas traiter, même disponible.
#
# RÈGLE DE LA PARTITION (sorties) : les stocks de sortie doivent former une PARTITION
#   de la capability : les modèles couverts par les différentes sorties ne se
#   chevauchent pas (disjoints) et, ensemble, couvrent TOUS les modèles produits.
#   Autrement dit, pour chaque pièce qui sort il existe EXACTEMENT un stock de sortie
#   capable de la recevoir — ni zéro (pièce bloquée), ni deux (ambiguïté).
#
# OPÉRATEURS & CONSOMMABLES : une tâche peut exiger des ressources (voir plus bas).
#   Chaque catégorie a une PORTÉE (scope) :
#     - PER_PIECE : par pièce du lot (la quantité est multipliée par la taille du lot).
#     - PER_BATCH : une fois par lot, quelle que soit sa taille.
#     - PER_TASK  : une fois pour toute la durée de vie du poste (ressource mobilisée).
#   Contraintes : les opérateurs ne peuvent PAS être PER_PIECE ; les consommables ne
#   peuvent PAS être PER_TASK.
#
# DÉMARRAGE (startup) : avant de traiter, un poste peut devoir se préparer — une
#   startup_duration et d'éventuels startup_operators. Après une panne ou un arrêt
#   programmé, le poste est « éteint » et devra redémarrer (re-payer ce temps).
#
# independent_carriers : False (défaut) => le poste attend qu'un lot soit terminé
#   avant d'en commencer un autre (séquentiel). True => il peut enchaîner / faire
#   avancer plusieurs lots en parallèle (dans la limite de max_capacity).
#
# (Operation et Carrier ci-dessous sont la mécanique interne qui porte un lot à
#  travers ces étapes ; le concepteur n'a pas à les manipuler directement.)

class Operation(sim.Component):
    def setup(self, duration: float) -> None:
        self.duration = duration
        self.complete = sim.State(value=False)

    def process(self):
        self.hold(self.duration)
        self.complete.set(True)


class Carrier(sim.Component):
    def setup(self, task: Task, task_duration: float) -> None:
        self.task = task
        self.task_duration = task_duration
        self.loaded_pieces: list[Piece] = []
        self.batch_collector = None
        self.claimed_resources = []
        self.allow_loading = sim.State(value=False)
        self.allow_dispatch = sim.State(value=False)
        self.loaded = sim.State(value=False)
        self.done = sim.State(value=False)

    def _broken(self) -> bool:
        return self.task.is_in_breakdown.get()

    def _abort(self):
        if self.batch_collector is not None and not self.batch_collector.done.get():
            self.batch_collector.cancel()

        if self.batch_collector is not None:
            pieces = list(self.batch_collector.collected_pieces)
            self.batch_collector.release_reserved_slots()
            if not self.batch_collector.done.get():
                self.batch_collector.cancel()
        else:
            pieces = list(self.loaded_pieces)

        if self.claimed_resources:
            self.release()
            self.claimed_resources = []
        if pieces:
            if self.batch_collector is not None:
                self.batch_collector.release_reserved_slots()
        if pieces and self.task.breakdown_bufs_out:
            placer = PiecePlacer(pieces=pieces, bufs_out=self.task.breakdown_bufs_out)
            self.wait((placer.done, True))

        self.done.set(True)
        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)

    def process(self):
        self.wait((self.allow_loading, True), (self.task.is_in_breakdown, True))
        if self._broken():
            self._abort()
            return

        self.batch_collector = self.task.config.batch_collector(task=self.task)
        self.wait((self.batch_collector.done, True), (self.task.is_in_breakdown, True))
        if self._broken():
            self._abort()
            return

        self.loaded.set(True)
        self.loaded_pieces = self.batch_collector.collected_pieces

        self.wait((self.allow_dispatch, True), (self.task.is_in_breakdown, True))
        if self._broken():
            self._abort(); return

        resources_to_request = []
        for resource, _ in self.task.config.resources:
            if hasattr(resource, "restock"):
                resource.restock(self)

        if self.task.config.resources_scope is Scope.PER_BATCH:
            for resource, quantity in self.task.config.resources:
                resources_to_request.append((resource, quantity))
        elif self.task.config.resources_scope is Scope.PER_PIECE:
            for resource, quantity in self.task.config.resources:
                resources_to_request.append((resource, quantity * len(self.loaded_pieces)))
        if self.task.config.operators_scope is Scope.PER_BATCH:
            for operator, quantity in self.task.config.operators:
                resources_to_request.append((operator, quantity))

        self.request(*resources_to_request)
        self.claimed_resources = resources_to_request

        if self.task.has_breakdown:
            operation = Operation(duration=self.task_duration)
            self.wait((operation.complete, True), (self.task.is_in_breakdown, True))
            if not operation.complete.get() and self._broken():
                operation.cancel()
                self._abort()
                return
        else:
            self.hold(self.task_duration)

        self.release()
        self.claimed_resources = []
        self.task.notify_slot_freed()
        piece_placer = PiecePlacer(pieces=self.loaded_pieces, bufs_out=self.task.bufs_out)
        self.wait((piece_placer.done, True))
        self.done.set(True)
        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)


class Scope(Enum):
    PER_PIECE = auto()
    PER_BATCH = auto()
    PER_TASK = auto()


@dataclass
class TaskConfig:
    capability: list[Model]
    operators: list[tuple[sim.Resource, int]]
    operators_scope: Scope
    resources: list[tuple[sim.Resource, float]]
    resources_scope: Scope
    task_duration: sim.Distribution
    startup_duration: sim.Distribution
    startup_operators: list[tuple[sim.Resource, int]]
    min_capacity: int
    max_capacity: int
    batch_collector: type[BatchCollector]
    independent_carriers: bool
    scheduled_shutdowns: ScheduledShutdowns | None


class Task(sim.Component, PickyPieceTaker):
    def setup(self, config: TaskConfig, bufs_in: list[HardBuffer], bufs_out: list[Buffer]):
        if config.operators_scope is Scope.PER_PIECE:
            raise ValueError("Operators scope must be PER_BATCH or PER_TASK")

        if config.resources_scope is Scope.PER_TASK:
            raise ValueError("Resources scope must be PER_PIECE or PER_BATCH")

        flushable_models: list[Model] = []
        for i in range(len(bufs_out)):
            flushable_models += bufs_out[i].valid_models
            for j in range(i + 1, len(bufs_out)):
                if not PickyPieceTaker.disjoint(bufs_out[i], bufs_out[j]):
                    raise ValueError("Out buffers must be a partition of task capability")

        PickyPieceTaker.__init__(self, config.capability)

        if not self.can_flush_into(PickyPieceTaker(flushable_models)):
            raise ValueError("Task must be able to flush out all models in its capability")

        self.config = config
        self.bufs_in = bufs_in
        self.bufs_out = bufs_out

        self.active_carriers: list[Carrier] = []
        self.vacant_slots = sim.Resource(capacity=config.max_capacity)

        self.started_up = sim.State(value=False)
        self.is_in_breakdown = sim.State(value=False)
        self.is_in_scheduled_shutdown = sim.State(value=False)
        self.has_breakdown = False
        self.breakdown_bufs_out = None
        # Signal incrémenté quand une place de traitement se libère (seuls les
        # collecteurs altruistes l'écoutent).
        self.slot_signal = None

    def notify_slot_freed(self) -> None:
        if self.slot_signal is not None:
            self.slot_signal.set(self.slot_signal.value() + 1)

    def process(self):
        while True:
            if self.is_in_breakdown.get():
                if self.config.operators_scope is Scope.PER_TASK:
                    self.release()
                self.wait((self.is_in_breakdown, False))

            if not self.started_up.get():
                self.request(*self.config.startup_operators)
                self.hold(self.config.startup_duration.sample())
                self.release(*self.config.startup_operators)
                self.started_up.set(True)
                if self.config.operators_scope is Scope.PER_TASK:
                    self.request(*self.config.operators)

            task_duration = self.config.task_duration.sample()
            carrier = Carrier(task=self, task_duration=task_duration)
            self.active_carriers.append(carrier)
            if self.config.scheduled_shutdowns is not None:
                while (next_shutdown := self.config.scheduled_shutdowns.next_shutdown()) is not None:
                    if env.now() + task_duration <= next_shutdown.start:
                        break
                    if self.config.operators_scope is Scope.PER_TASK:
                        self.release()
                    self.hold(till=next_shutdown.start, cap_now=True)
                    self.is_in_scheduled_shutdown.set(True)
                    self.hold(till=next_shutdown.end)
                    self.is_in_scheduled_shutdown.set(False)
                    self.started_up.set(False)

            carrier.allow_loading.set(True)
            self.wait((carrier.loaded, True), (self.is_in_breakdown, True))

            if self.is_in_breakdown.get():
                continue

            carrier.allow_dispatch.set(True)

            if not self.config.independent_carriers:
                self.wait((carrier.done, True), (self.is_in_breakdown, True))
                if self.is_in_breakdown.get():
                    continue


# LA SOURCE — FirstTask
# ----------------------
# Le point d'entrée des pièces dans l'atelier. En boucle, elle : tire une durée
# (intervalle entre deux créations), choisit un MODÈLE selon des probabilités que
# vous fixez, attend la durée, puis dépose la nouvelle pièce dans ses stocks de
# sortie. Elle peut aussi consommer des ressources à chaque création (matière
# première, par exemple).
# Règles : probabilités des modèles dans [0, 1] et de somme 1 ; les stocks de sortie
# doivent former une PARTITION des modèles générés (cf. règle de partition de Task).
@dataclass
class FirstTaskConfig:
    models_probs: list[tuple[Model, float]]
    resources: list[tuple[sim.Resource, float]]
    task_duration: sim.Distribution


class FirstTask(sim.Component, PickyPieceTaker):
    def setup(self, config: FirstTaskConfig, bufs_out: list[Buffer]) -> None:
        if not all(0 <= prob <= 1 for _, prob in config.models_probs):
            raise ValueError("Probabilities in first task must be in [0, 1]")

        if not abs(sum(prob for _, prob in config.models_probs) - 1) < 1e-9:
            raise ValueError("Probabilities in first task must sum to 1")

        flushable_models: list[Model] = []

        for i in range(len(bufs_out)):
            flushable_models += bufs_out[i].valid_models

            for j in range(i + 1, len(bufs_out)):
                if not PickyPieceTaker.disjoint(bufs_out[i], bufs_out[j]):
                    raise ValueError("Out buffers must be a partition of first task models")

        self.models = [m for m, _ in config.models_probs]
        self.probs = [p for _, p in config.models_probs]
        self.bufs_out = bufs_out
        self.config = config

        PickyPieceTaker.__init__(self, self.models)

        if not PickyPieceTaker.same_valid_models(PickyPieceTaker(flushable_models), self):
            raise ValueError("First task must be able to flush out all models")

    def process(self):
        while True:
            task_duration = self.config.task_duration.sample()

            resources_to_request = []

            for resource, quantity in self.config.resources:
                if hasattr(resource, "restock"):
                    resource.restock(self)
                resources_to_request.append((resource, quantity))

            if resources_to_request:
                self.request(*resources_to_request)

            model = np.random.choice(self.models, p=self.probs)
            new_piece = Piece(model=model)
            self.hold(task_duration)
            piece_placer = PiecePlacer(pieces=[new_piece], bufs_out=self.bufs_out)

            self.wait((piece_placer.done, True))



####################################
# BREAKDOWNS & SCHEDULED SHUTDOWNS #
####################################
#
# LES ALÉAS : PANNES ET ARRÊTS PROGRAMMÉS
# ---------------------------------------
# PANNES (Breakdown) — se rattache à UNE tâche, avec deux lois :
#   - MTBF (Mean Time Between Failures) : temps de bon fonctionnement avant la panne.
#   - MTTR (Mean Time To Repair)        : durée de réparation.
#   Pendant la panne, le lot en cours est INTERROMPU et les pièces sont évacuées vers
#   les stocks de sortie de la panne (bufs_out) : c'est là qu'on modélise les REBUTS
#   ou un réacheminement vers une zone de reprise. Après réparation, le poste doit
#   redémarrer.
#
# ARRÊTS PROGRAMMÉS (ScheduledShutdowns + Interval) — arrêts PLANIFIÉS (pauses, nuits,
#   maintenance...), par opposition aux pannes aléatoires. Un Interval est une fenêtre
#   [début, fin] (début <= fin) ; un ScheduledShutdowns regroupe plusieurs intervalles
#   qui doivent être DISJOINTS. À l'approche d'un arrêt, la tâche finit proprement ce
#   qu'elle peut, se met en pause (en libérant ses opérateurs PER_TASK), puis redémarre.

class Breakdown(sim.Component):
    def setup(self, task: Task, mtbf: sim.Distribution, mttr: sim.Distribution, bufs_out: list[Buffer]) -> None:
        self.task = task
        self.mtbf = mtbf
        self.mttr = mttr
        self.bufs_out = bufs_out
        task.has_breakdown = True
        task.breakdown_bufs_out = bufs_out

    def process(self):
        while True:
            self.wait((self.task.is_in_scheduled_shutdown, False))
            self.hold(self.mtbf.sample())

            if self.task.is_in_scheduled_shutdown.get():
                continue

            self.task.is_in_breakdown.set(True)
            self.hold(self.mttr.sample())
            self.task.is_in_breakdown.set(False)
            self.task.started_up.set(False)


class ScheduledShutdowns:
    def __init__(self, intervals: list[Interval]) -> None:
        for int1 in intervals:
            for int2 in intervals:
                if int1 is int2:
                    continue
                if not Interval.disjoint(int1, int2):
                    raise ValueError("Scheduled breakdown intervals must be disjoint")

        self.intervals = sorted(intervals, key=lambda x: x.start)

    def next_shutdown(self) -> Interval | None:
        for interval in self.intervals:
            if interval.end > env.now():
                return interval
        return None



########################
# RESTOCKABLE RESOURCE #
########################
#
# LES RESSOURCES
# --------------
# RESSOURCE RÉUTILISABLE (sim.Resource, paramétrée côté flux) — un pool de capacité
#   (ex. « 3 opérateurs »). Les tâches en empruntent une partie le temps de
#   travailler, puis la rendent ; si elle n'est pas disponible, la tâche attend son
#   tour. C'est l'outil pour modéliser une contention (plusieurs postes se disputant
#   un nombre limité de personnes ou de machines).
#
# CONSOMMABLE RÉAPPROVISIONNABLE (RestockableResource) — une ressource qui se VIDE
#   (matière première, composants...) et se réapprovisionne automatiquement : elle a
#   une capacité (stock plein) et un seuil (threshold) ; dès que le niveau passe sous
#   le seuil, une commande part et, après delivery_duration, le stock revient à sa
#   capacité. Seuil bas => ruptures possibles ; seuil haut => on commande tôt et souvent.

class Delivery(sim.Component):
    def setup(self, stock: RestockableResource, delivery_duration):
        self.stock = stock
        self.delivery_duration = delivery_duration

    def process(self):
        self.hold(self.delivery_duration)
        missing = self.stock.capacity.value - self.stock.available_quantity()
        if missing > 0:
            self.request((self.stock, -missing))
        self.stock.active_order = False


class RestockableResource(sim.Resource):
    def setup(self, order_duration: sim.Distribution, delivery_duration: sim.Distribution, threshold: float) -> None:
        self.order_duration = order_duration
        self.delivery_duration = delivery_duration
        self.threshold = threshold
        self.active_order = False

    def restock(self, demander: sim.Component):
        if not self.active_order and self.available_quantity() < self.threshold:
            self.active_order = True
            demander.hold(self.delivery_duration)
            Delivery(stock=self, delivery_duration=self.delivery_duration)
