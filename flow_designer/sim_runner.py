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
    os.chdir(REPO_ROOT)
    try:
        import matplotlib
        matplotlib.use("Agg")
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
            stride = max(1.0, criterion.time / 1000.0)
        elif isinstance(criterion, ByPiecesProduced):
            meta.update(criterion="ByPiecesProduced", goal=criterion.total)
            if criterion.timeout != float("inf"):
                meta["timeout"] = criterion.timeout
            stride = 30.0
        else:
            emit("ERROR", {"message": f"unknown stopping criterion {type(criterion).__name__}"})
            return 1


        generator = getattr(parser, "piece_generator", None)
        gap = getattr(generator, "gap", None) if generator is not None else None
        if isinstance(gap, (int, float)):
            meta["gap"] = gap
            criterion_block = parser.data.get("stopping_criterion", {})
            meta["gap_mode"] = ("manual" if not isinstance(criterion, ByPiecesProduced)
                                or "gap" in criterion_block else "automatic")
        elif generator is not None:
            meta["gap_mode"] = "function"

        emit("META", meta)

        def snapshot() -> dict:
            out = {"sim_now": env.now(), "elapsed": perf_counter() - started}
            if isinstance(criterion, ByPiecesProduced):
                out["pieces"] = len(criterion.exit_buffer)
            return out


        last_emit = 0.0
        while not criterion.done():
            slice_started = perf_counter()
            env.run(duration=stride)
            if env.peek() == float("inf") and not criterion.done():


                break
            now = perf_counter()
            if now - last_emit >= 0.1:
                emit("PROGRESS", snapshot())
                last_emit = now
            if now - slice_started < 0.005:
                stride = min(stride * 2, 1440.0)

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
