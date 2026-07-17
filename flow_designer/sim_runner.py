"""Headless simulation runner behind the designer's Run simulation dialog.

Usage: python sim_runner.py <flow.json>

Loads the flow through the parser, runs the simulation in short slices and
prints machine-readable progress to stdout, one '@@TAG {json}' line at a time:

    @@META {...}      once, after loading: criterion type + totals
    @@PROGRESS {...}  during the run: sim time, elapsed wall time, pieces
    @@DONE {...}      once, after the report is written: the report directory
    @@ERROR {...}     on a fatal error, before exiting nonzero

Everything else on stdout/stderr (warnings, tracebacks) is free-form; consumers
must only trust the tagged lines. The report lands in runs/<stamp>_<stem> under
the repository root, exactly like a main.py run.
"""

import json
import os
import sys
import traceback
from time import perf_counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def emit(tag: str, payload: dict) -> None:
    print(f"@@{tag} {json.dumps(payload)}", flush=True)


def main(argv: list) -> int:
    if len(argv) != 2:
        emit("ERROR", {"message": "usage: sim_runner.py <flow.json>"})
        return 2
    json_path = os.path.abspath(argv[1])

    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    os.chdir(REPO_ROOT)  # the report's runs/ directory lands in the repo root
    try:
        import matplotlib
        matplotlib.use("Agg")  # the report only saves figures; never open windows
    except Exception:
        pass

    try:
        from parser.parser import Parser
        from simulation import env
        from simulation.judgement_day import ByTime, ByPiecesProduced

        started = perf_counter()
        parser = Parser(json_path)
        parser.load_all()
        criterion = parser.stopping_criterion

        meta = {"file": json_path, "sim_start": parser.data["start_date"]}
        if isinstance(criterion, ByTime):
            meta.update(criterion="ByTime", total_time=criterion.time)
            stride = max(1.0, criterion.time / 1000.0)  # ~1000 progress points
        elif isinstance(criterion, ByPiecesProduced):
            meta.update(criterion="ByPiecesProduced", goal=criterion.total)
            if criterion.timeout != float("inf"):
                meta["timeout"] = criterion.timeout
            stride = 30.0  # sim minutes per slice; grows when slices turn out empty
        else:
            emit("ERROR", {"message": f"unknown stopping criterion {type(criterion).__name__}"})
            return 1
        emit("META", meta)

        def snapshot() -> dict:
            out = {"sim_now": env.now(), "elapsed": perf_counter() - started}
            if isinstance(criterion, ByPiecesProduced):
                out["pieces"] = len(criterion.exit_buffer)
            return out

        # Run in slices so progress can be reported from outside the simulation:
        # a component holding inside the sim would keep the event queue alive
        # forever on a stalled model, whereas slicing preserves the plain-run
        # semantics (the stopper activates main, run() returns early).
        last_emit = 0.0
        while not criterion.done():
            slice_started = perf_counter()
            env.run(duration=stride)
            if env.peek() == float("inf") and not criterion.done():
                # nothing scheduled anymore: the factory can never move again
                # (e.g. every shift ended with an unmet goal and no timeout), so
                # a plain env.run() would have returned here; do the same
                break
            now = perf_counter()
            if now - last_emit >= 0.1:
                emit("PROGRESS", snapshot())
                last_emit = now
            if now - slice_started < 0.005:
                stride = min(stride * 2, 1440.0)  # empty stretch: stride up to a day

        emit("PROGRESS", snapshot())
        report_dir = parser.report()
        emit("DONE", {"report_dir": os.path.abspath(report_dir), **snapshot()})
        return 0
    except Exception as error:
        traceback.print_exc()
        emit("ERROR", {"message": f"{type(error).__name__}: {error}"})
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
