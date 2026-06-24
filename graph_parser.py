"""
Advanced graph parser.

Same as graph_parser.py, but:
* runs on the performance-optimized engine (simulation_optimized.py),
* loads Monitor cards exported by flow_designer_advanced.py and binds each one to a
  hard buffer, and
* exposes print_statistics(), which reports the selected statistics for every
  monitor once the simulation has finished.

Monitor statistics available (toggled per Monitor card):
  - avg_length               time-average number of pieces in the buffer
  - max_length               peak number of pieces in the buffer
  - length_std               time-weighted std-dev of the buffer length
  - current_length           number of pieces left in the buffer at the end
  - avg_stay                 average time a piece spends inside the buffer
  - max_stay                 longest time a piece spent inside the buffer
  - avg_time_before_arrival  average lead time from piece creation to entering it
  - throughput               number of pieces that have passed through the buffer
"""

import json
from time import perf_counter

from simulation import *


# Canonical statistic keys -> human label. Order defines print order.
MONITOR_STAT_LABELS = {
    "avg_length": "Average length",
    "max_length": "Max length",
    "length_std": "Length std-dev",
    "current_length": "Final length",
    "avg_stay": "Average stay time",
    "max_stay": "Max stay time",
    "avg_time_before_arrival": "Avg time before arrival",
    "throughput": "Throughput (pieces seen)",
}

# Statistics that are enabled when a Monitor card does not specify a value for them.
MONITOR_STAT_DEFAULTS = {
    "avg_length": True,
    "max_length": True,
    "length_std": False,
    "current_length": False,
    "avg_stay": True,
    "max_stay": False,
    "avg_time_before_arrival": True,
    "throughput": True,
}


class BufferMonitor:
    """Binds a named monitor to a hard buffer and computes its statistics on demand."""

    def __init__(self, name: str, buffer: HardBuffer, stats: dict[str, bool]) -> None:
        self.name = name
        self.buffer = buffer
        self.stats = stats

        # The "time before arrival" stat needs per-piece tallying inside the engine.
        self.arrival_monitor = None
        if stats.get("avg_time_before_arrival"):
            self.arrival_monitor = sim.Monitor(
                name=f"time_before_arrival.{name}", type="float"
            )
            buffer.arrival_monitors.append(self.arrival_monitor)

    def compute(self) -> dict[str, float]:
        buf = self.buffer
        out: dict[str, float] = {}

        for key, label in MONITOR_STAT_LABELS.items():
            if not self.stats.get(key):
                continue

            if key == "avg_length":
                out[label] = buf.length.mean()
            elif key == "max_length":
                out[label] = buf.length.maximum()
            elif key == "length_std":
                out[label] = buf.length.std()
            elif key == "current_length":
                out[label] = len(buf)
            elif key == "avg_stay":
                out[label] = buf.length_of_stay.mean()
            elif key == "max_stay":
                out[label] = buf.length_of_stay.maximum()
            elif key == "avg_time_before_arrival":
                out[label] = self.arrival_monitor.mean() if self.arrival_monitor else float("nan")
            elif key == "throughput":
                out[label] = buf.length_of_stay.number_of_entries()

        return out


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
                anonymous=True,
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
                    (self.restockable_resources[resource["resource"]], resource["quantity"]) for resource in node["resources"]
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


        # Load monitors
        self.load_monitors()

    def load_monitors(self) -> None:
        """Create a BufferMonitor for each Monitor card bound to a hard buffer.

        For performance, salabim's length / length_of_stay monitoring is turned OFF
        on every hard buffer and re-enabled only for buffers that are actually
        observed by a monitor.
        """
        self.monitors: list[BufferMonitor] = []
        monitored_ids: set[str] = set()

        for node in self.per_kind.setdefault("Monitor", []):
            name = node.get("name", node["id"])
            buffer_id = node.get("buffer")

            if not buffer_id or buffer_id not in self.hard_buffers:
                print(f"[WARNING] Monitor '{name}' is not connected to a hard buffer; skipping.")
                continue

            buffer = self.hard_buffers[buffer_id]
            stats = self.resolve_monitor_stats(node.get("stats", {}))

            buffer.monitor(True)  # ensure length / length_of_stay are tallied
            monitored_ids.add(buffer_id)
            self.monitors.append(BufferMonitor(name, buffer, stats))

        for buffer_id, buffer in self.hard_buffers.items():
            if buffer_id not in monitored_ids:
                buffer.monitor(False)  # skip unused stat collection

    @staticmethod
    def resolve_monitor_stats(raw: dict) -> dict[str, bool]:
        return {
            key: bool(raw.get(key, default))
            for key, default in MONITOR_STAT_DEFAULTS.items()
        }

    @staticmethod
    def _fmt(value) -> str:
        if value is None:
            return "n/a"
        try:
            if value != value:  # NaN
                return "n/a"
        except Exception:
            return str(value)
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    def print_statistics(self) -> None:
        """Print the selected statistics for every monitor. Call after env.run()."""
        header = f" Monitor statistics @ t = {env.now():g} "
        print("=" * len(header))
        print(header)
        print("=" * len(header))

        if not self.monitors:
            print("No monitors defined.")
            return

        for monitor in self.monitors:
            print(f"\n[{monitor.name}]  ->  buffer: {monitor.buffer.name()}")
            stats = monitor.compute()

            if not stats:
                print("  (no statistics enabled)")
                continue

            width = max(len(label) for label in stats)
            for label, value in stats.items():
                print(f"  {label.ljust(width)} : {self._fmt(value)}")


if __name__ == "__main__":
    graph_parser = GraphParser("clean_export.json")
    start = perf_counter()
    env.run(till=10000)
    print(f"Duration: {perf_counter() - start}s")
    graph_parser.print_statistics()
