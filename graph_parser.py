import json
from simulation import *
from time import perf_counter


class GraphParser:
    DISTRIBUTION_TYPE_TO_CLASS = {
        "Constant": sim.Constant,
        "Normal": sim.Normal,
        "Triangular": sim.Triangular,
        "Exponential": sim.Exponential,
    }

    SCOPE_NAME_TO_ENUM = {
        "PER_PIECE": Scope.PER_PIECE,
        "PER_BATCH": Scope.PER_BATCH,
        "PER_TASK": Scope.PER_TASK
    }

    COLLECTOR_TYPE_TO_CLASS = {
        "GreedyBatchCollector": GreedyBatchCollector,
        "AltruisticBatchCollector": AltruisticBatchCollector
    }

    def __init__(self, filename: str) -> None:
        self.filename = filename

        with open(self.filename, 'r') as f:
            self.data = json.load(f)

        self.discriminate()
        self.load_classes()

    def discriminate(self) -> None:
        self.per_kind = {}
        for node in self.data["nodes"]:
            kind = node["kind"]
            if kind not in self.per_kind:
                self.per_kind[kind] = []
            self.per_kind[kind].append(node)

    def get_hard_soft_buffer(self, id: str) -> Buffer:
        if id in self.hard_buffers:
            return self.hard_buffers[id]
        return self.soft_buffers[id]

    def load_classes(self) -> None:
        # Load models
        self.models: dict[str, Model] = {}

        for model in self.data["models"]:
            name = model["name"]
            self.models[name] = Model(name=name)

        for model in self.data["models"]:
            name = model["name"]
            parent = model["parent"]
            if parent is not None:
                self.models[name].parent = self.models[parent]


        # Load distributions
        self.distributions: dict[str, sim.Distribution] = {}

        for node in self.per_kind.setdefault("Distribution", []):
            type = node["distribution"]["type"]
            args = node["distribution"]["params"].values()
            self.distributions[node["id"]] = GraphParser.DISTRIBUTION_TYPE_TO_CLASS[type](*args)


        # Load resources
        self.resources: dict[str, sim.Resource] = {}

        for node in self.per_kind.setdefault("Resource", []):
            self.resources[node["id"]] = sim.Resource(
                name=node["name"],
                capacity=node["capacity"],
                anonymous=node["anonymous"]
            )


        # Load restockable resources
        self.restockable_resources: dict[str, RestockableResource] = {}

        for node in self.per_kind.setdefault("RestockableResource", []):
            self.restockable_resources[node["id"]] = RestockableResource(
                name=node["name"],
                capacity=node["capacity"],
                order_duration=self.distributions[node["order_duration"]],
                delivery_duration=self.distributions[node["delivery_duration"]],
                threshold=node["threshold"]
            )


        # Load intervals
        self.intervals: dict[str, Interval] = {}

        for node in self.per_kind.setdefault("Interval", []):
            self.intervals[node["id"]] = Interval(node["start"], node["end"])


        # Load scheduled shutdowns
        self.scheduled_shutdowns: dict[str, ScheduledShutdowns] = {}

        for node in self.per_kind.setdefault("ScheduledShutdowns", []):
            intervals = [self.intervals[id] for id in node["intervals"]]
            self.scheduled_shutdowns[node["id"]] = ScheduledShutdowns(intervals)


        # Load hard buffers
        self.hard_buffers: dict[str, HardBuffer] = {}

        for node in self.per_kind.setdefault("HardBuffer", []):
            self.hard_buffers[node["id"]] = HardBuffer(
                name=node["name"],
                valid_models=[self.models[model] for model in node["valid_models"]]
            )


        # Load soft buffers
        self.soft_buffers: dict[str, SoftBuffer] = {}

        for node in self.per_kind.setdefault("SoftBuffer", []):
            self.soft_buffers[node["id"]] = SoftBuffer()

        for node in self.per_kind.setdefault("SoftBuffer", []):
            bufs_out = [self.get_hard_soft_buffer(buffer_prob["buffer"]) for buffer_prob in node["buffer_probs"]]
            probs = [buffer_prob["probability"] for buffer_prob in node["buffer_probs"]]
            self.soft_buffers[node["id"]].init(list(zip(bufs_out, probs)))


        # Load first tasks
        self.first_tasks: dict[str, FirstTask] = {}

        for node in self.per_kind.setdefault("FirstTask", []):
            config = FirstTaskConfig(
                models_probs=[
                    (self.models[model_prob["model"]], model_prob["probability"]) for model_prob in node["models_probs"]
                ],
                resources=[
                    (self.resources[resource["resource"]], resource["quantity"]) for resource in node["resources"]
                ],
                task_duration=self.distributions[node["task_duration"]]
            )
            self.first_tasks[node["id"]] = FirstTask(
                name=node["name"],
                config=config,
                bufs_out=[self.get_hard_soft_buffer(id) for id in node["bufs_out"]]
            )


        # Load tasks
        self.tasks: dict[str, Task] = {}

        for node in self.per_kind.setdefault("Task", []):
            config = TaskConfig(
                capability=[self.models[model] for model in node["capability"]],
                operators=[(self.resources[op["resource"]], op["quantity"]) for op in node["operators"]],
                operators_scope=GraphParser.SCOPE_NAME_TO_ENUM[node["operators_scope"]],
                resources=[(self.restockable_resources[rsrc["resource"]], rsrc["quantity"]) for rsrc in node["resources"]],
                resources_scope=GraphParser.SCOPE_NAME_TO_ENUM[node["resources_scope"]],
                task_duration=self.distributions[node["task_duration"]],
                startup_duration=self.distributions[node["startup_duration"]],
                startup_operators=[(self.resources[op["resource"]], op["quantity"]) for op in node["startup_operators"]],
                min_capacity=node["min_capacity"],
                max_capacity=node["max_capacity"],
                batch_collector=GraphParser.COLLECTOR_TYPE_TO_CLASS[node["batch_collector"]],
                independent_carriers=node["independent_carriers"],
                scheduled_shutdowns=self.scheduled_shutdowns[node["scheduled_shutdowns"]] if node["scheduled_shutdowns"] else None,
            )
            self.tasks[node["id"]] = Task(
                name=node["name"],
                config=config,
                bufs_in=[self.hard_buffers[id] for id in node["bufs_in"]],
                bufs_out=[self.get_hard_soft_buffer(id) for id in node["bufs_out"]]
            )


        # Load breakdowns
        self.breakdowns: dict[str, Breakdown] = {}

        for node in self.per_kind.setdefault("Breakdown", []):
            self.breakdowns[node["id"]] = Breakdown(
                task=self.tasks[node["task"]],
                mtbf=self.distributions[node["mtbf"]],
                mttr=self.distributions[node["mttr"]],
                bufs_out=[self.get_hard_soft_buffer(id) for id in node["bufs_out"]]
            )


graph_parser = GraphParser("atelier_injection.json")
start = perf_counter()
env.run(till=100)
print(f"Duration: {perf_counter() - start}s")