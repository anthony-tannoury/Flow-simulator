import salabim as sim

from .triggerable import Triggerable


class Model:
    def __init__(self, name: str, parent: Model | None = None) -> None:
        self.name = name
        self.parent = parent
        self.children: list[Model] = []

        if self.parent is not None:
            self.parent.children.append(self)


class Piece(sim.Component):
    ID = 0

    def setup(self, model: Model) -> None:
        self.model = model
        self.id = str(Piece.ID).zfill(6)
        Piece.ID += 1

    def enter(self, q, priority = None):
        if isinstance(q, Triggerable):
            q.trigger.trigger()
        return super().enter(q, priority)


class PickyPieceTaker:
    def __init__(self, valid_models: list[Model]) -> None:
        if not valid_models:
            raise ValueError("PickyPieceTaker must have at least one valid model")

        self.valid_models = valid_models

    def can_take(self, obj: Piece | Model) -> bool:
        model = obj.model if isinstance(obj, Piece) else obj
        can_take = False
        while model is not None and not can_take:
            can_take |= model in self.valid_models
            model = model.parent
        return can_take

    def can_flush_into(self, ppt: PickyPieceTaker) -> bool:
        return all(ppt.can_take(model) for model in self.valid_models)

    def disjoint(self, other: PickyPieceTaker) -> bool:
        return not (any(self.can_take(model) for model in other.valid_models)
                    or any(other.can_take(model) for model in self.valid_models))
