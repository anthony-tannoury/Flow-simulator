from __future__ import annotations

import copy
import json
import os
import platform
import re
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, List, Tuple

from Qt import QtCore, QtGui, QtWidgets
from NodeGraphQt import BaseNode, NodeGraph, PropertiesBinWidget

try:
    from . import results_mode
except ImportError:
    import results_mode

try:
    from .ui_helpers import APP_NAME, EDITOR_VERSION, PIECE_POLICY_OPTIONS, POLICY_OPTIONS, SHUTDOWN_TYPES, _DialogHeightCapper, _apply_ref_map, _check_date_intervals, _model_parents, _names, _shift_export_shape, _taker_can_take, _takers_disjoint, app_settings, as_float, as_int, bundled_cpp_engine, connect_ports_by_name, connected_nodes_from_port, connected_refs_from_port, cpp_engine_filename, ensure_ids, get_connected_ports, get_output_refs, get_property_json, is_valid_connection, new_uid, node_kind, node_uid, parse_date, parse_date_time, port_signature, qmessage, sentence_case, to_canonical
except ImportError:
    from ui_helpers import APP_NAME, EDITOR_VERSION, PIECE_POLICY_OPTIONS, POLICY_OPTIONS, SHUTDOWN_TYPES, _DialogHeightCapper, _apply_ref_map, _check_date_intervals, _model_parents, _names, _shift_export_shape, _taker_can_take, _takers_disjoint, app_settings, as_float, as_int, bundled_cpp_engine, connect_ports_by_name, connected_nodes_from_port, connected_refs_from_port, cpp_engine_filename, ensure_ids, get_connected_ports, get_output_refs, get_property_json, is_valid_connection, new_uid, node_kind, node_uid, parse_date, parse_date_time, port_signature, qmessage, sentence_case, to_canonical

try:
    from .nodes import BreakdownNode, BufferNode, PieceGeneratorNode, ResourceTaskNode, RouterNode, ShutdownsNode, TaskNode
except ImportError:
    from nodes import BreakdownNode, BufferNode, PieceGeneratorNode, ResourceTaskNode, RouterNode, ShutdownsNode, TaskNode

try:
    from .dialogs import BreakdownMenuDialog, BufferMenuDialog, ClosingDaysRegistryDialog, GeneratorMenuDialog, ModelRegistryDialog, OperatorRegistryDialog, PieceTaskMenuDialog, ResourceRegistryDialog, ResourceTaskMenuDialog, RouterMenuDialog, RunSimulationDialog, ShiftRegistryDialog, ShutdownsMenuDialog, SimulationSettingsDialog
except ImportError:
    from dialogs import BreakdownMenuDialog, BufferMenuDialog, ClosingDaysRegistryDialog, GeneratorMenuDialog, ModelRegistryDialog, OperatorRegistryDialog, PieceTaskMenuDialog, ResourceRegistryDialog, ResourceTaskMenuDialog, RouterMenuDialog, RunSimulationDialog, ShiftRegistryDialog, ShutdownsMenuDialog, SimulationSettingsDialog


class FlowEditorWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1400, 850)


        self.current_path = None
        self._dirty = False
        self._suspend_dirty = False


        self.results = None
        self._last_run_dir = None
        self._results_toolbar = None
        self._results_dock = None
        self._saved_node_colors = {}
        self._update_title()

        self.graph = NodeGraph()


        try:
            self.graph.set_acyclic(False)
        except Exception:
            pass


        try:
            from NodeGraphQt.constants import PipeLayoutEnum
            self.graph.set_pipe_style(PipeLayoutEnum.CURVED.value)
        except Exception:
            try:
                self.graph.set_pipe_style(0)
            except Exception:
                pass

        self.model_registry = []
        self.resource_registry = []
        self.operator_registry = []
        self.shift_registry = []
        self.closing_day_registry = []
        self.stopping_criterion = {}
        self.start_date = "01-01-2026 00:00"
        self.seed = 0

        self.graph.register_nodes([
            ShutdownsNode,
            BufferNode,
            RouterNode,
            PieceGeneratorNode,
            TaskNode,
            ResourceTaskNode,
            BreakdownNode,
        ])

        self.setCentralWidget(self.graph.widget)

        self.properties_bin = PropertiesBinWidget(node_graph=self.graph)
        self.properties_dock = QtWidgets.QDockWidget("Properties", self)
        self.properties_dock.setWidget(self.properties_bin)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.properties_dock)

        self.properties_dock.hide()

        self._build_menus()
        self._install_context_menus()
        self._connect_signals()
        self.statusBar().showMessage("Ready. Use the Create menu to add nodes.")

    def _build_menus(self):


        self._edit_actions = []

        def editing(action):
            self._edit_actions.append(action)
            return action

        file_menu = self.menuBar().addMenu("File")
        act_new = editing(file_menu.addAction("New"))
        act_new.setShortcut(QtGui.QKeySequence.New)
        act_open = editing(file_menu.addAction("Open..."))
        act_open.setShortcut(QtGui.QKeySequence.Open)
        file_menu.addSeparator()
        act_save = editing(file_menu.addAction("Save"))
        act_save.setShortcut(QtGui.QKeySequence.Save)
        act_save_as = editing(file_menu.addAction("Save as..."))
        act_save_as.setShortcut(QtGui.QKeySequence.SaveAs)
        act_new.triggered.connect(lambda checked=False: self.new_graph())
        act_open.triggered.connect(lambda checked=False: self.open_file_dialog())
        act_save.triggered.connect(lambda checked=False: self.save_file())
        act_save_as.triggered.connect(lambda checked=False: self.save_file_as())

        registries_menu = self.menuBar().addMenu("Registries")
        editing(registries_menu.addAction("Edit models...")).triggered.connect(self.edit_models)
        editing(registries_menu.addAction("Edit resources...")).triggered.connect(self.edit_resources)
        editing(registries_menu.addAction("Edit operators...")).triggered.connect(self.edit_operators)
        editing(registries_menu.addAction("Edit closing days...")).triggered.connect(self.edit_closing_days)
        editing(registries_menu.addAction("Edit shifts...")).triggered.connect(self.edit_shifts)

        simulation_menu = self.menuBar().addMenu("Simulation")
        editing(simulation_menu.addAction("Settings...")).triggered.connect(self.edit_simulation_settings)
        act_run = editing(simulation_menu.addAction("Run simulation..."))
        act_run.setShortcut("F5")
        act_run.triggered.connect(lambda checked=False: self.run_simulation())


        engine_menu = simulation_menu.addMenu("Engine")
        backend = app_settings().value("engine/backend", "python")
        self._act_engine_py = engine_menu.addAction("Python")
        self._act_engine_cpp = engine_menu.addAction("C++ (native)")
        for act, name in ((self._act_engine_py, "python"), (self._act_engine_cpp, "cpp")):
            act.setCheckable(True)
            act.setChecked(backend == name)
            act.triggered.connect(lambda checked=False, n=name: self._choose_engine(n))
        engine_menu.addSeparator()
        engine_menu.addAction("Select C++ executable...").triggered.connect(
            lambda checked=False: self._pick_cpp_executable())

        results_menu = self.menuBar().addMenu("Results")
        self.act_view_last_results = results_menu.addAction("View last run results")
        self.act_view_last_results.setEnabled(False)
        self.act_view_last_results.triggered.connect(
            lambda checked=False: self._last_run_dir and self.enter_results_mode(self._last_run_dir))
        results_menu.addAction("Open run results...").triggered.connect(
            lambda checked=False: self.open_results_dialog())
        results_menu.addSeparator()
        self.act_exit_results = results_menu.addAction("Exit results mode")
        self.act_exit_results.setEnabled(False)
        self.act_exit_results.triggered.connect(lambda checked=False: self.exit_results_mode())

        edit_menu = self.menuBar().addMenu("Edit")
        copy_action = editing(edit_menu.addAction("Copy cards"))
        copy_action.setShortcut(QtGui.QKeySequence.Copy)
        copy_action.triggered.connect(lambda: self.copy_selected_cards())
        cut_action = editing(edit_menu.addAction("Cut cards"))
        cut_action.setShortcut(QtGui.QKeySequence.Cut)
        cut_action.triggered.connect(lambda: self.cut_selected_cards())
        paste_action = editing(edit_menu.addAction("Paste cards"))
        paste_action.setShortcut(QtGui.QKeySequence.Paste)
        paste_action.triggered.connect(self.paste_cards)
        dup_action = editing(edit_menu.addAction("Duplicate cards"))
        dup_action.setShortcut("Ctrl+D")
        dup_action.triggered.connect(lambda: self.duplicate_selected_cards())
        edit_menu.addSeparator()
        disable_action = editing(edit_menu.addAction("Disable / enable cards"))
        disable_action.setShortcut("Ctrl+E")
        disable_action.triggered.connect(lambda: self.toggle_disable_selected_cards())
        edit_menu.addSeparator()
        delete_action = editing(edit_menu.addAction("Delete selected"))
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected_nodes)

        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction("Validate graph").triggered.connect(self.validate_graph_dialog)
        tools_menu.addAction("Frame all").triggered.connect(self.frame_all)
        tools_menu.addSeparator()
        tools_menu.addAction(self.properties_dock.toggleViewAction())

        create_menu = self.menuBar().addMenu("Create")
        for label, cls_name in [
            ("Shutdowns", "simulation.flow.ShutdownsNode"),
            ("Buffer", "simulation.flow.BufferNode"),
            ("Router", "simulation.flow.RouterNode"),
            ("Piece generator", "simulation.flow.PieceGeneratorNode"),
            ("Piece task", "simulation.flow.TaskNode"),
            ("Resource task", "simulation.flow.ResourceTaskNode"),
            ("Breakdown", "simulation.flow.BreakdownNode"),
        ]:
            action = editing(create_menu.addAction(label))
            action.triggered.connect(lambda checked=False, t=cls_name: self.create_node(t))

    def _install_context_menus(self):
        try:
            graph_menu = self.graph.get_context_menu("graph")
            graph_menu.add_command("Copy cards", lambda graph: self.copy_selected_cards())
            graph_menu.add_command("Cut cards", lambda graph: self.cut_selected_cards())
            graph_menu.add_command("Paste cards", lambda graph: self.paste_cards())
            graph_menu.add_command("Duplicate cards", lambda graph: self.duplicate_selected_cards())
            graph_menu.add_command("Disable / enable cards",
                                   lambda graph: self.toggle_disable_selected_cards())
            nodes_menu = self.graph.get_context_menu("nodes")
            for cls in (ShutdownsNode, BufferNode, RouterNode, PieceGeneratorNode,
                        TaskNode, ResourceTaskNode, BreakdownNode):
                node_type = f"{cls.__identifier__}.{cls.__name__}"
                nodes_menu.add_command(
                    "Copy cards",
                    func=lambda graph, node: self.copy_selected_cards(context_node=node),
                    node_type=node_type)
                nodes_menu.add_command(
                    "Cut cards",
                    func=lambda graph, node: self.cut_selected_cards(context_node=node),
                    node_type=node_type)
                nodes_menu.add_command(
                    "Duplicate cards",
                    func=lambda graph, node: self.duplicate_selected_cards(context_node=node),
                    node_type=node_type)
                nodes_menu.add_command(
                    "Disable / enable cards",
                    func=lambda graph, node: self.toggle_disable_selected_cards(context_node=node),
                    node_type=node_type)
        except Exception as error:
            print(f"[WARNING] Could not install context menus: {error}")

    def frame_all(self):
        nodes = self.all_nodes()
        if not nodes:
            return
        try:
            self.graph.clear_selection()
            for n in nodes:
                n.set_selected(True)
            self.graph.fit_to_selection()
            self.graph.clear_selection()
        except Exception:
            try:
                self.graph.center_on(nodes)
            except Exception:
                pass

    def _connect_signals(self):
        try:
            self.graph.node_double_clicked.connect(self.on_node_double_clicked)
        except Exception:
            pass
        try:
            self.graph.port_connected.connect(self.on_port_connected)
        except Exception:
            pass


        for signal_name in ("node_created", "nodes_deleted", "port_connected",
                            "port_disconnected", "property_changed"):
            try:
                getattr(self.graph, signal_name).connect(self.mark_dirty)
            except Exception:
                pass
        for signal_name in ("node_created", "nodes_deleted", "port_connected",
                            "port_disconnected"):
            try:
                getattr(self.graph, signal_name).connect(self._results_mutation_guard)
            except Exception:
                pass


    def mark_dirty(self, *args, **kwargs):
        if self._suspend_dirty or self._dirty or self.results is not None:
            return
        self._dirty = True
        self.setWindowModified(True)

    def set_clean(self):
        self._dirty = False
        self.setWindowModified(False)

    def is_dirty(self) -> bool:
        return self._dirty

    def _display_name(self) -> str:
        return os.path.basename(self.current_path) if self.current_path else "Untitled"

    def _update_title(self):
        suffix = "  [results]" if self.results is not None else ""
        self.setWindowTitle(f"{self._display_name()}{suffix}[*] - {APP_NAME}")
        self.setWindowModified(self._dirty)

    def maybe_save(self) -> bool:
        if not self._dirty:
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(f"Do you want to save the changes made to {self._display_name()}?")
        box.setInformativeText("Your changes will be lost if you don't save them.")
        box.setStandardButtons(QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
                               | QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Save)
        answer = box.exec()
        if answer == QtWidgets.QMessageBox.Save:
            return self.save_file()
        return answer == QtWidgets.QMessageBox.Discard

    def save_file(self) -> bool:
        if not self.current_path:
            return self.save_file_as()
        return self._write_to(self.current_path)

    def save_file_as(self) -> bool:
        suggested = self.current_path or "flow.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save flow JSON", suggested, "JSON (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        return self._write_to(path)

    def _write_to(self, path: str) -> bool:
        try:
            data = self.export_clean_json()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as error:
            qmessage(self, "Save failed", f"Could not save {path}:\n{error}",
                     QtWidgets.QMessageBox.Warning)
            return False
        self.current_path = path
        self.set_clean()
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}")
        return True

    def open_file_dialog(self):
        if not self.maybe_save():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open flow JSON", "", "JSON (*.json)")
        if not path:
            return
        self.open_file(path)

    def open_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as error:
            qmessage(self, "Open failed", f"Could not open {path}:\n{error}",
                     QtWidgets.QMessageBox.Warning)
            return
        self._suspend_dirty = True
        try:
            self.reset_session()
            self.load_clean_json(data)
        finally:
            self._suspend_dirty = False
        self.current_path = path
        self.set_clean()
        self._update_title()
        self.statusBar().showMessage(f"Opened {path}")

    def closeEvent(self, event):
        if self.maybe_save():
            event.accept()
        else:
            event.ignore()


    def open_results_dialog(self):
        start_dir = "runs" if os.path.isdir("runs") else ""
        run_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Open run results", start_dir)
        if run_dir:
            self.enter_results_mode(run_dir)

    def enter_results_mode(self, run_dir: str):
        try:
            results = results_mode.ResultsData(run_dir)
        except Exception as error:
            qmessage(self, "Open results failed", str(error), QtWidgets.QMessageBox.Warning)
            return
        if self.results is not None:
            self.exit_results_mode()


        canvas_ids = {node_uid(n) for n in self.all_nodes()}
        if self._dirty or not results.node_ids() <= canvas_ids:
            if not self.maybe_save():
                return
            snapshot = results.snapshot_path()
            try:
                with open(snapshot, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as error:
                qmessage(self, "Open results failed",
                         f"Could not load the run's flow snapshot:\n{error}",
                         QtWidgets.QMessageBox.Warning)
                return
            self._suspend_dirty = True
            try:
                self.reset_session()
                self.load_clean_json(data)
            finally:
                self._suspend_dirty = False
            self.current_path = None
            self.set_clean()

        self.results = results
        self._last_run_dir = run_dir
        self.act_view_last_results.setEnabled(True)
        self._lock_for_results(True)
        self._build_results_toolbar()
        self._results_dock = results_mode.ResultsDock(self, results)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._results_dock)
        self._apply_results_tooltips(True)
        self._props_dock_was_visible = self.properties_dock.isVisible()
        self.properties_dock.setVisible(False)
        self._update_title()
        self.statusBar().showMessage(
            "Results mode: cards are locked; double-click one for its stats.")

    def exit_results_mode(self):
        if self.results is None:
            return
        self._apply_heatmap(-1)
        self._apply_results_tooltips(False)
        self._lock_for_results(False)
        if self._results_toolbar is not None:
            self.removeToolBar(self._results_toolbar)
            self._results_toolbar.deleteLater()
            self._results_toolbar = None
        if self._results_dock is not None:
            self.removeDockWidget(self._results_dock)
            self._results_dock.deleteLater()
            self._results_dock = None
        self.properties_dock.setVisible(getattr(self, "_props_dock_was_visible", False))
        self.results = None
        self._update_title()
        self.statusBar().showMessage("Left results mode.")

    def _lock_for_results(self, lock: bool):
        for action in self._edit_actions:
            action.setEnabled(not lock)
        self.act_exit_results.setEnabled(lock)
        for node in self.all_nodes():
            try:
                flag = getattr(QtWidgets.QGraphicsItem, "GraphicsItemFlag",
                               QtWidgets.QGraphicsItem).ItemIsMovable
                node.view.setFlag(flag, not lock)
            except Exception:
                pass

    def _build_results_toolbar(self):
        bar = QtWidgets.QToolBar("Results")
        bar.setObjectName("results_toolbar")
        bar.setMovable(False)
        label = QtWidgets.QLabel("  " + self.results.run_label() + "  ")
        label.setStyleSheet("font-weight: bold;")
        bar.addWidget(label)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        bar.addWidget(spacer)
        bar.addWidget(QtWidgets.QLabel("Color by: "))
        combo = QtWidgets.QComboBox()
        combo.addItem("None", -1)
        for i, (metric_label, *_rest) in enumerate(results_mode.HEAT_METRICS):
            combo.addItem(metric_label, i)
        combo.currentIndexChanged.connect(lambda _i: self._apply_heatmap(combo.currentData()))
        bar.addWidget(combo)
        exit_btn = QtWidgets.QPushButton("Exit results mode")
        exit_btn.clicked.connect(self.exit_results_mode)
        bar.addWidget(exit_btn)
        self.addToolBar(QtCore.Qt.TopToolBarArea, bar)
        self._results_toolbar = bar

    def _apply_heatmap(self, metric_index: int):
        self._suspend_dirty = True
        try:

            for node in self.all_nodes():
                uid = node_uid(node)
                if uid in self._saved_node_colors:
                    try:
                        node.set_color(*self._saved_node_colors[uid])
                    except Exception:
                        pass
            self._saved_node_colors = {}
            if metric_index is None or metric_index < 0 or self.results is None:
                return
            colors = results_mode.heat_values(metric_index, self.results)
            for node in self.all_nodes():
                uid = node_uid(node)


                target = colors.get(uid, results_mode.DIMMED_COLOR)
                try:


                    current = node.get_property("color")
                    self._saved_node_colors[uid] = tuple(current)[:3]
                    node.set_color(*target)
                except Exception:
                    pass
        finally:
            self._suspend_dirty = False

    def _apply_results_tooltips(self, on: bool):
        for node in self.all_nodes():
            try:
                tip = results_mode.card_tooltip(node_kind(node), node_uid(node), self.results) if on else None
                node.view.setToolTip(tip or "")
            except Exception:
                pass

    def _results_mutation_guard(self, *args, **kwargs):
        if self.results is not None and not self._suspend_dirty:
            self._on_results_mutation()

    def _on_results_mutation(self):
        self.exit_results_mode()
        self.mark_dirty()
        self.statusBar().showMessage("Graph changed: left results mode (the report "
                                     "no longer matches the canvas).")

    def run_simulation(self):
        if self._dirty and self.current_path:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Run simulation")
            box.setIcon(QtWidgets.QMessageBox.Question)
            box.setText(f"{self._display_name()} has unsaved changes.")
            box.setInformativeText("The simulation runs the saved file, so the changes "
                                   "must be saved first. Save and run?")
            box.setStandardButtons(QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel)
            box.setDefaultButton(QtWidgets.QMessageBox.Save)
            if box.exec() != QtWidgets.QMessageBox.Save:
                return
        if self._dirty or not self.current_path:
            if not self.save_file():
                return
        problems = self.validate_graph()
        if problems:
            answer = QtWidgets.QMessageBox.question(
                self, "Validation warnings",
                "The graph has validation warnings; the run may fail.\nRun anyway?\n\n"
                + "\n".join(problems[:12]))
            if answer != QtWidgets.QMessageBox.Yes:
                return
        cpp_exe = None
        if app_settings().value("engine/backend", "python") == "cpp":
            cpp_exe = self._resolve_cpp_engine()
            if cpp_exe is None:
                return
        dlg = RunSimulationDialog(self, self.current_path, cpp_exe=cpp_exe)
        dlg.exec()
        if dlg.report_dir:
            self._last_run_dir = dlg.report_dir
            self.act_view_last_results.setEnabled(True)
            if dlg.view_results_requested:
                self.enter_results_mode(dlg.report_dir)


    def _resolve_cpp_engine(self) -> str | None:
        settings = app_settings()
        custom = settings.value("engine/cpp_path", "")
        if custom and os.path.isfile(custom):
            return custom
        bundled = bundled_cpp_engine()
        if bundled:
            return bundled
        answer = QtWidgets.QMessageBox.question(
            self, "C++ engine not found",
            f"No bundled C++ engine for this platform (expected "
            f"engines/{cpp_engine_filename()}).\n\nSelect an executable to use?",
            QtWidgets.QMessageBox.Open | QtWidgets.QMessageBox.Cancel)
        if answer == QtWidgets.QMessageBox.Open:
            return self._pick_cpp_executable()
        return None

    def _pick_cpp_executable(self) -> str | None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select the C++ engine executable")
        if path:
            app_settings().setValue("engine/cpp_path", path)
        return path or None

    def _choose_engine(self, backend: str) -> None:
        app_settings().setValue("engine/backend", backend)
        self._act_engine_py.setChecked(backend == "python")
        self._act_engine_cpp.setChecked(backend == "cpp")
        if backend == "cpp" and self._resolve_cpp_engine_quiet() is None:
            QtWidgets.QMessageBox.information(
                self, "C++ engine",
                f"No bundled engine found (engines/{cpp_engine_filename()}). Use "
                "“Engine → Select C++ executable...” to point at one, or you'll "
                "be asked when you run.")

    def _resolve_cpp_engine_quiet(self) -> str | None:
        settings = app_settings()
        custom = settings.value("engine/cpp_path", "")
        if custom and os.path.isfile(custom):
            return custom
        return bundled_cpp_engine()

    def all_nodes(self) -> List[BaseNode]:
        try:
            return list(self.graph.all_nodes())
        except Exception:
            return list(self.graph.nodes())

    def current_view_center(self):
        try:
            viewer = self.graph.viewer()
            center = viewer.mapToScene(viewer.viewport().rect().center())
            return center.x(), center.y()
        except Exception:
            return 0.0, 0.0

    def _node_rect(self, node):
        try:
            r = node.view.sceneBoundingRect()
            if r.width() > 1 and r.height() > 1:
                return (r.left(), r.top(), r.right(), r.bottom())
        except Exception:
            pass
        try:
            x, y = node.x_pos(), node.y_pos()
        except Exception:
            return None
        return (x, y, x + 240.0, y + 200.0)

    def content_bounds(self, nodes=None):
        if nodes is None:
            nodes = self.all_nodes()
        rects = [r for r in (self._node_rect(n) for n in nodes) if r is not None]
        if not rects:
            return None
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))

    def shift_nodes(self, nodes, dx, dy):
        if not dx and not dy:
            return
        for n in nodes:
            try:
                x, y = n.x_pos(), n.y_pos()
                self.set_node_position_safe(n, x + dx, y + dy)
            except Exception:
                pass

    def create_node(self, node_type: str):
        node = self.graph.create_node(node_type)
        x, y = self.current_view_center()
        self.set_node_position_safe(node, x, y)
        return node

    def new_graph(self):
        if not self.maybe_save():
            return
        self._suspend_dirty = True
        try:
            self.reset_session()
        finally:
            self._suspend_dirty = False
        self.current_path = None
        self.set_clean()
        self._update_title()

    def reset_session(self):
        self.graph.clear_session()
        self.model_registry = []
        self.resource_registry = []
        self.operator_registry = []
        self.shift_registry = []
        self.closing_day_registry = []
        self.stopping_criterion = {}
        self.start_date = "01-01-2026 00:00"
        self.seed = 0

    @staticmethod
    def _ids_by_name(entries):
        return {e.get("name"): e.get("id") for e in entries if e.get("id")}

    def _mark_dirty_if_changed(self, before, after):
        if before != after:
            self.mark_dirty()

    def edit_models(self):
        dlg = ModelRegistryDialog(self, self.model_registry)
        if dlg.exec():
            try:
                before = copy.deepcopy(self.model_registry)
                self.model_registry = ensure_ids(dlg.models(), "model", self._ids_by_name(self.model_registry))
                self._mark_dirty_if_changed(before, self.model_registry)
                self.statusBar().showMessage(f"{len(self.model_registry)} models defined.")
            except Exception as e:
                qmessage(self, "Invalid models", str(e), QtWidgets.QMessageBox.Warning)

    def edit_resources(self):
        dlg = ResourceRegistryDialog(self, self.resource_registry)
        if dlg.exec():
            before = copy.deepcopy(self.resource_registry)
            self.resource_registry = ensure_ids(dlg.entries(), "resource", self._ids_by_name(self.resource_registry))
            self._mark_dirty_if_changed(before, self.resource_registry)
            self.statusBar().showMessage(f"{len(self.resource_registry)} resources defined.")

    def edit_operators(self):
        shift_names = [s.get("name", "") for s in self.shift_registry if s.get("name")]
        dlg = OperatorRegistryDialog(self, self.operator_registry, shift_names=shift_names)
        if dlg.exec():
            before = copy.deepcopy(self.operator_registry)
            self.operator_registry = ensure_ids(dlg.entries(), "operator", self._ids_by_name(self.operator_registry))
            self._mark_dirty_if_changed(before, self.operator_registry)
            self.statusBar().showMessage(f"{len(self.operator_registry)} operator groups defined.")

    def edit_closing_days(self):
        dlg = ClosingDaysRegistryDialog(self, self.closing_day_registry)
        if dlg.exec():
            before = copy.deepcopy(self.closing_day_registry)
            old_by_date = {e.get("date"): e.get("id") for e in self.closing_day_registry if e.get("id")}
            self.closing_day_registry = ensure_ids(dlg.entries(), "closingday", old_by_date, key="date")
            self._mark_dirty_if_changed(before, self.closing_day_registry)
            self.statusBar().showMessage(f"{len(self.closing_day_registry)} closing days defined.")

    def edit_shifts(self):
        dlg = ShiftRegistryDialog(self, self.shift_registry, closing_days=self.closing_day_registry)
        if dlg.exec():
            before = copy.deepcopy(self.shift_registry)
            self.shift_registry = ensure_ids(dlg.entries(), "shift", self._ids_by_name(self.shift_registry))
            self._mark_dirty_if_changed(before, self.shift_registry)
            self.statusBar().showMessage(f"{len(self.shift_registry)} shift definitions.")

    def edit_simulation_settings(self):
        dlg = SimulationSettingsDialog(self, self.start_date, self.stopping_criterion,
                                       self.model_registry, self.seed)
        if dlg.exec():
            before = (self.start_date, self.seed, copy.deepcopy(self.stopping_criterion))
            self.start_date = dlg.start_value()
            self.seed = dlg.seed_value()
            self.stopping_criterion = dlg.value()
            self._mark_dirty_if_changed(before, (self.start_date, self.seed, self.stopping_criterion))
            label = sentence_case(self.stopping_criterion.get("type") or "?")
            start = self.start_date or "not set"
            self.statusBar().showMessage(f"Simulation: start {start}; seed {self.seed}; stops on {label}.")

    def on_node_double_clicked(self, node):
        kind = node_kind(node)
        if self.results is not None:
            dlg = results_mode.card_dialog(self, kind, node_uid(node), node.name(), self.results)
            if dlg is None:
                self.statusBar().showMessage(f"No run stats for {kind} cards.")
            else:
                dlg.exec()
            return
        dlg = None
        if kind == "Shutdowns":
            dlg = ShutdownsMenuDialog(self, node)
        elif kind == "Buffer":
            dlg = BufferMenuDialog(self, node, self.model_registry)
        elif kind == "Router":
            dlg = RouterMenuDialog(self, node)
        elif kind == "PieceGenerator":
            dlg = GeneratorMenuDialog(self, node, _names(self.shift_registry))
        elif kind == "Breakdown":
            dlg = BreakdownMenuDialog(self, node)
        elif kind == "Task":
            dlg = PieceTaskMenuDialog(self, node, self)
        elif kind == "ResourceTask":
            dlg = ResourceTaskMenuDialog(self, node, self)
        if dlg is not None and dlg.exec():
            dlg.apply()

    def on_port_connected(self, *args):
        ports = [a for a in args if hasattr(a, "node") and hasattr(a, "name")]
        if len(ports) < 2:
            return
        p1, p2 = ports[0], ports[1]
        k1, d1, name1 = port_signature(p1)
        k2, d2, name2 = port_signature(p2)
        if d1 == "output" and d2 == "input":
            out_p, in_p = p1, p2
            out_kind, out_name, in_kind, in_name = k1, name1, k2, name2
        elif d2 == "output" and d1 == "input":
            out_p, in_p = p2, p1
            out_kind, out_name, in_kind, in_name = k2, name2, k1, name1
        else:
            return
        if not is_valid_connection(out_kind, out_name, in_kind, in_name):
            try:
                out_p.disconnect_from(in_p)
            except Exception:
                pass
            qmessage(self, "Invalid connection",
                     f"Cannot connect {out_kind}.{out_name} to {in_kind}.{in_name}.",
                     QtWidgets.QMessageBox.Warning)

    def connections_clean(self) -> List[dict]:
        result = []
        for node in self.all_nodes():
            try:
                outputs = node.outputs()
            except Exception:
                continue
            if not outputs:
                continue
            if isinstance(outputs, dict):
                output_items = outputs.items()
            else:
                output_items = []
                for port in outputs:
                    try:
                        output_items.append((port.name(), port))
                    except Exception:
                        pass
            for out_name, out_port in output_items:
                for in_port in get_connected_ports(out_port):
                    try:
                        target_node = in_port.node()
                        result.append({
                            "from_node": node_uid(node),
                            "from_kind": node_kind(node),
                            "from_port": out_name,
                            "to_node": node_uid(target_node),
                            "to_kind": node_kind(target_node),
                            "to_port": in_port.name(),
                        })
                    except Exception:
                        pass
        return result

    def export_clean_json(self) -> dict:
        nodes = []
        for node in self.all_nodes():
            if hasattr(node, "to_clean_json"):
                node_data = self._card_json(node)
                if node_data and node_data.get("kind"):
                    nodes.append(node_data)


        ensure_ids(self.model_registry, "model")
        ensure_ids(self.resource_registry, "resource")
        ensure_ids(self.operator_registry, "operator")
        ensure_ids(self.shift_registry, "shift")
        ensure_ids(self.closing_day_registry, "closingday", key="date")
        models = copy.deepcopy(self.model_registry)
        resources = copy.deepcopy(self.resource_registry)
        operators = copy.deepcopy(self.operator_registry)
        closing_days = copy.deepcopy(self.closing_day_registry)
        shifts = [_shift_export_shape(copy.deepcopy(s)) for s in self.shift_registry]
        name_to_id = {
            "model": {m["name"]: m["id"] for m in models if m.get("name") and m.get("id")},
            "resource": {r["name"]: r["id"] for r in resources if r.get("name") and r.get("id")},
            "operator": {o["name"]: o["id"] for o in operators if o.get("name") and o.get("id")},
            "shift": {s["name"]: s["id"] for s in shifts if s.get("name") and s.get("id")},
            "closing_day": {c["date"]: c["id"] for c in closing_days if c.get("date") and c.get("id")},
        }
        criterion = copy.deepcopy(self.stopping_criterion)
        _apply_ref_map(nodes, models, resources, operators,
                       lambda kind, v: name_to_id[kind].get(v, v), shifts=shifts, criterion=criterion)
        return {
            "editor": {"name": APP_NAME, "version": EDITOR_VERSION, "format": "clean-json"},
            "models": models,
            "resources": resources,
            "operators": operators,
            "closing_days": closing_days,
            "shifts": shifts,
            "stopping_criterion": criterion,
            "start_date": self.start_date,
            "seed": self.seed,
            "nodes": nodes,
            "connections": self.connections_clean(),
        }

    def validate_graph_dialog(self):
        problems = self.validate_graph()
        if not problems:
            qmessage(self, "Validation", "No validation problems found.")
        else:
            qmessage(self, "Validation problems", "\n".join(problems[:50]), QtWidgets.QMessageBox.Warning)

    def _outlet_valid_models(self, node):
        kind = node_kind(node)
        if kind == "Buffer":
            vm = get_property_json(node, "valid_models", [])
            return set(vm) if vm else None
        if kind == "Router":
            sets = []
            for b in connected_nodes_from_port(node, "to_buffers", "output"):
                if not self._node_enabled(b):
                    continue
                if node_kind(b) != "Buffer":
                    return None
                vm = get_property_json(b, "valid_models", [])
                if not vm:
                    return None
                sets.append(set(vm))
            return set.intersection(*sets) if sets else None
        return None

    def _check_flushability(self, node, giver_models, out_port, label, problems):
        outlets = [o for o in connected_nodes_from_port(node, out_port, "output")
                   if self._node_enabled(o)]
        if not outlets or not giver_models:
            return
        resolved = []
        for o in outlets:
            vm = self._outlet_valid_models(o)
            if vm is None:
                return
            resolved.append((o, vm))
        parents = _model_parents(self.model_registry)
        for i in range(len(resolved)):
            for j in range(i + 1, len(resolved)):
                if not _takers_disjoint(resolved[i][1], resolved[j][1], parents):
                    problems.append(f"{label} '{node.name()}': outlets '{resolved[i][0].name()}' and "
                                    f"'{resolved[j][0].name()}' accept overlapping models (outlets must be disjoint).")
        union = set().union(*[vm for _, vm in resolved])
        for m in giver_models:
            if not _taker_can_take(union, m, parents):
                problems.append(f"{label} '{node.name()}': model '{m}' has no outlet that can accept it.")

    def validate_graph(self) -> List[str]:
        problems = []
        start_dt = parse_date_time(self.start_date)


        disabled = {node_uid(n) for n in self.all_nodes() if not self._node_enabled(n)}
        for c in self.connections_clean():
            if c["from_node"] in disabled or c["to_node"] in disabled:
                continue
            if not is_valid_connection(c["from_kind"], c["from_port"], c["to_kind"], c["to_port"]):
                problems.append(f"Invalid connection: {c['from_kind']}.{c['from_port']} -> {c['to_kind']}.{c['to_port']}")

        for node in self.all_nodes():
            if node_uid(node) in disabled:
                continue
            kind = node_kind(node)
            name = node.name()
            if kind in ("Task", "ResourceTask"):

                pol = get_property_json(node, "policies", {})
                expected = PIECE_POLICY_OPTIONS if kind == "Task" else POLICY_OPTIONS
                for pname, (options, _default) in expected.items():
                    ptype = pol.get(pname, {}).get("type")
                    if ptype is None:
                        problems.append(f"'{name}': missing protocol '{pname}'.")
                    elif ptype not in options:
                        problems.append(f"'{name}': protocol '{pname}' has unknown type '{ptype}'.")

                if (pol.get("task_shift_constraint", {}).get("type") == "ConstrainedByShift"
                        and pol.get("pending_carrier_pre_task_shift_end", {}).get("type") == "WaitForCarriers"):
                    problems.append(f"'{name}': ConstrainedByShift cannot be combined with "
                                    f"WaitForCarriers on pending_carrier_pre_task_shift_end.")

                if node.has_property("priority") and str(node.get_property("priority")) != "":
                    if not 0 <= as_int(node.get_property("priority")) <= 10:
                        problems.append(f"'{name}': task priority must be in [0, 10].")

                prod = {o.get("name"): json.dumps(o.get("productivity"), sort_keys=True)
                        for o in self.operator_registry}
                for field in ("operators", "loading_operators", "startup_operators"):
                    for group in get_property_json(node, field, []):
                        seen = {prod.get(g.get("operator")) for g in group if g.get("operator") in prod}
                        if len(seen) > 1:
                            problems.append(f"'{name}': operators in one alternative of '{field}' "
                                            f"must share the same productivity.")
                            break
            if kind == "Buffer":


                buffer_type = node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE"
                if buffer_type == "PASSAGE":
                    consumers = [t for t in connected_nodes_from_port(node, "to_task", "output")
                                 if node_kind(t) == "Task" and self._node_enabled(t)]
                    if not consumers:
                        problems.append(f"Buffer '{name}': no task consumes from this buffer (dead end).")
                    else:
                        parents = _model_parents(self.model_registry)
                        children = {}
                        for m in self.model_registry:
                            children.setdefault(m.get("parent"), []).append(m["name"])

                        def leaves_under(model_name):
                            subs = children.get(model_name)
                            if not subs:
                                return [model_name]
                            return [leaf for s in subs for leaf in leaves_under(s)]

                        takeable = set()
                        for t in consumers:
                            takeable.update(mc.get("model") for mc in get_property_json(t, "models_configs", []))
                        uncovered = []
                        for model_name in get_property_json(node, "valid_models", []):
                            for leaf in leaves_under(model_name):
                                if leaf not in uncovered and not _taker_can_take(takeable, leaf, parents):
                                    uncovered.append(leaf)
                        for leaf in uncovered:
                            problems.append(f"Buffer '{name}': model '{leaf}' can enter but no "
                                            f"connected task can take it.")
            if kind == "Router":


                if not [b for b in connected_nodes_from_port(node, "to_buffers", "output")
                        if self._node_enabled(b)]:
                    problems.append(f"Router '{name}' has no outlets.")
                branch_map = {bid: p for bid, p in get_property_json(node, "buffer_probs", {}).items()
                              if bid not in disabled}
                branches = list(branch_map.values())
                freeloaders = [p for p in branches if p is None]
                if len(freeloaders) > 1:
                    problems.append(f"Router '{name}': at most one freeloader branch is allowed.")

                targets = {node_uid(b): b.name()
                           for b in connected_nodes_from_port(node, "to_buffers", "output")
                           if self._node_enabled(b)}
                for bid, p in branch_map.items():
                    if isinstance(p, dict) and p.get("kind") == "constant" and as_float(p.get("value", 0.0)) == 0.0:
                        problems.append(f"Router '{name}': branch '{targets.get(bid, bid)}' has "
                                        f"probability 0 (dead branch; no piece can ever take it).")
                consts = [p.get("value", 0.0) for p in branches
                          if isinstance(p, dict) and p.get("kind") == "constant"]
                if any(not 0 <= v <= 1 for v in consts):
                    problems.append(f"Router '{name}': branch probabilities must be in [0, 1].")
                if branches and all(p is None or (isinstance(p, dict) and p.get("kind") == "constant")
                                    for p in branches):
                    s = sum(consts)
                    if not freeloaders and abs(s - 1.0) > 1e-6:
                        problems.append(f"Router '{name}': branch probabilities sum to {s:g} "
                                        f"(must sum to 1, or mark one branch as the freeloader).")
                    elif freeloaders and s > 1 + 1e-6:
                        problems.append(f"Router '{name}': branch probabilities sum to {s:g} "
                                        f"(must be <= 1 so the freeloader can take the rest).")
            if kind == "Task":
                if not [r for r in connected_refs_from_port(node, "bufs_in", "input") if r not in disabled]:
                    problems.append(f"Piece task '{name}' has no input buffers.")
                if not [r for r in get_output_refs(node, "bufs_out") if r not in disabled]:
                    problems.append(f"Piece task '{name}' has no output buffers.")
                mc = get_property_json(node, "models_configs", [])
                if not mc:
                    problems.append(f"Piece task '{name}' has no model configs.")
                else:
                    for m in mc:
                        if not m.get("duration"):
                            problems.append(f"Piece task '{name}' model '{m.get('model')}' has no duration.")


                    cap = as_float(node.get_property("max_capacity") if node.has_property("max_capacity") else 1.0, 1.0)
                    contiguous = bool(node.get_property("contiguous_carriers")) if node.has_property("contiguous_carriers") else False
                    for m in mc:
                        mn = as_float(m.get("min_carrier_capacity", 1))
                        mx = as_float(m.get("max_carrier_capacity", 1))
                        if cap < mn:
                            problems.append(f"Piece task '{name}': max_capacity {cap:g} is smaller than "
                                            f"min_carrier_capacity {mn:g} (model '{m.get('model')}'); "
                                            f"carriers can never collect their minimum batch.")
                        elif not contiguous and cap < mx:
                            problems.append(f"Piece task '{name}': non-contiguous carriers reserve "
                                            f"max_carrier_capacity {mx:g} slots (model '{m.get('model')}') "
                                            f"but max_capacity is {cap:g}; the collector deadlocks "
                                            f"waiting for slots that cannot exist.")

                    ct = str(node.get_property("collector_type") if node.has_property("collector_type") else "")
                    if ct.startswith("NON_DISCRIMINATING"):
                        for f, lbl in [("duration", "duration"),
                                       ("min_carrier_capacity", "min_carrier_capacity"),
                                       ("max_carrier_capacity", "max_carrier_capacity")]:
                            if len({json.dumps(m.get(f), sort_keys=True) for m in mc}) > 1:
                                problems.append(f"Piece task '{name}': a non-discriminating collector requires "
                                                f"all models to share the same {lbl}.")
                    self._check_flushability(node, [m.get("model") for m in mc if m.get("model")],
                                             "bufs_out", "Piece task", problems)

            elif kind == "ResourceTask":
                if not get_property_json(node, "duration", None):
                    problems.append(f"Resource task '{name}' has no duration.")

                cap = as_float(node.get_property("max_capacity") if node.has_property("max_capacity") else 1.0, 1.0)
                contiguous = bool(node.get_property("contiguous_carriers")) if node.has_property("contiguous_carriers") else False
                mn = as_float(node.get_property("min_carrier_capacity") if node.has_property("min_carrier_capacity") else 1.0, 1.0)
                mx = as_float(node.get_property("max_carrier_capacity") if node.has_property("max_carrier_capacity") else 1.0, 1.0)
                if cap < mn:
                    problems.append(f"Resource task '{name}': max_capacity {cap:g} is smaller than "
                                    f"min_carrier_capacity {mn:g}; carriers can never collect "
                                    f"their minimum batch.")
                elif not contiguous and cap < mx:
                    problems.append(f"Resource task '{name}': non-contiguous carriers reserve "
                                    f"max_carrier_capacity {mx:g} slots but max_capacity is {cap:g}; "
                                    f"the collector deadlocks waiting for slots that cannot exist.")
                outs = get_property_json(node, "resources_out", [])
                if not outs:
                    problems.append(f"Resource task '{name}' has no output resources.")
                for out in outs:
                    if as_float(out.get("lowerbound", 0.0)) < 0:
                        problems.append(f"Resource task '{name}': output '{out.get('resource')}' "
                                        f"lowerbound must be ≥ 0.")
                    ub = out.get("upperbound", 1.0)
                    if ub in ("inf", "Infinity") or as_float(ub, 1.0) == float("inf"):
                        problems.append(f"Resource task '{name}': output '{out.get('resource')}' "
                                        f"upperbound must be finite.")

                tr = get_property_json(node, "transformed_resources", [])
                props = [as_float(t.get("proportion", 0.0)) for t in tr]
                if not tr:
                    problems.append(f"Resource task '{name}': needs transformed resources whose proportions "
                                    f"sum to 1 (the simulation rejects an empty set).")
                elif any(p < 0 or p > 1 for p in props):
                    problems.append(f"Resource task '{name}': transformed-resource proportions must be in [0, 1].")
                elif abs(sum(props) - 1.0) > 1e-6:
                    problems.append(f"Resource task '{name}': transformed-resource proportions must sum to 1 "
                                    f"(currently {sum(props):g}).")

            elif kind == "Breakdown":
                tasks = [t for t in connected_nodes_from_port(node, "breakdown", "output")
                         if self._node_enabled(t)]
                if not tasks:
                    problems.append(f"Breakdown '{name}' is not attached to a task.")
                if not (get_property_json(node, "mtbf", {}) or {}):
                    problems.append(f"Breakdown '{name}' has no mtbf set.")
                if not get_property_json(node, "mttr", None):
                    problems.append(f"Breakdown '{name}' has no mttr distribution.")
                has_outlets = bool([r for r in get_output_refs(node, "bufs_out") if r not in disabled])
                for t in tasks:
                    if node_kind(t) == "Task" and not has_outlets:
                        problems.append(f"Breakdown '{name}' on piece task '{t.name()}' must have "
                                        f"lifeboat outlets for in-progress pieces.")
                    elif node_kind(t) == "ResourceTask" and has_outlets:
                        problems.append(f"Breakdown '{name}' on resource task '{t.name()}' cannot have outlets.")

            elif kind == "PieceGenerator":


                if not [r for r in get_output_refs(node, "bufs_out") if r not in disabled]:
                    problems.append(f"Piece generator '{name}' has no outlets.")
                if not get_property_json(node, "shifts", []):
                    problems.append(f"Piece generator '{name}' has no shifts (double-click it to choose when it emits).")


                frontier = [o for o in connected_nodes_from_port(node, "bufs_out", "output")
                            if self._node_enabled(o)]
                seen = set()
                while frontier:
                    outlet = frontier.pop()
                    if id(outlet) in seen:
                        continue
                    seen.add(id(outlet))
                    okind = node_kind(outlet)
                    if (okind == "Buffer" and outlet.has_property("buffer_type")
                            and outlet.get_property("buffer_type") == "SCRAP"):
                        problems.append(f"Piece generator '{name}': its outlet chain reaches SCRAP "
                                        f"buffer '{outlet.name()}' (generated pieces would be "
                                        f"scrapped on arrival).")
                    elif okind == "Router":
                        frontier.extend(connected_nodes_from_port(outlet, "to_buffers", "output"))

            elif kind == "Buffer":
                if not get_property_json(node, "valid_models", []):
                    problems.append(f"Buffer '{name}' has no valid models.")

            elif kind == "Router":
                out_bufs = [b for b in connected_nodes_from_port(node, "to_buffers", "output")
                            if self._node_enabled(b)]
                if not out_bufs:
                    problems.append(f"Router '{name}' has no output buffers.")
                else:
                    vmsets = [set(get_property_json(b, "valid_models", []))
                              for b in out_bufs if node_kind(b) == "Buffer"]
                    if vmsets and all(vmsets) and not set.intersection(*vmsets):
                        problems.append(f"Router '{name}': its buffers share no common valid model "
                                        f"(router outlets must overlap).")

            elif kind == "Shutdowns":
                mode = node.get_property("mode") if node.has_property("mode") else "custom"
                if mode == "generator":
                    g = get_property_json(node, "generator", {})
                    g_start = parse_date_time(g.get("start"))
                    g_end = parse_date_time(g.get("end"))
                    if g_start is None or g_end is None:
                        problems.append(f"Shutdowns '{name}': generator dates must be 'dd-mm-yyyy hh:mm'.")
                    else:
                        if start_dt is not None and g_start < start_dt:
                            problems.append(f"Shutdowns '{name}': generator starts before the "
                                            f"simulation start date.")
                        if g_end <= g_start:
                            problems.append(f"Shutdowns '{name}': generator start must be before its end.")
                    in_between = as_float(g.get("in_between", 0.0))
                    duration = as_float(g.get("duration", 0.0))
                    if in_between <= 0:
                        problems.append(f"Shutdowns '{name}': 'in between' must be > 0 minutes.")
                    if duration <= 0:
                        problems.append(f"Shutdowns '{name}': duration must be > 0 minutes.")
                    if in_between > 0 and duration > in_between:
                        problems.append(f"Shutdowns '{name}': duration exceeds 'in between'; "
                                        f"consecutive shutdowns would overlap (the simulation "
                                        f"rejects overlapping intervals).")
                else:
                    _check_date_intervals(f"Shutdowns '{name}'",
                                          get_property_json(node, "intervals", []),
                                          start_dt, problems)


        for label, reg in (("model", self.model_registry), ("resource", self.resource_registry),
                           ("operator", self.operator_registry), ("shift", self.shift_registry)):
            names = [e.get("name") for e in reg if e.get("name")]
            dupes = sorted({n for n in names if names.count(n) > 1})
            for n in dupes:
                problems.append(f"Two or more {label} registry entries are named '{n}'; "
                                f"names must be unique so cards can reference them.")


        cd_dates = [e.get("date") for e in self.closing_day_registry]
        for d in cd_dates:
            if parse_date(d) is None:
                problems.append(f"Closing day '{d}': date must be 'dd-mm-yyyy'.")
        for d in sorted({d for d in cd_dates if d and cd_dates.count(d) > 1}):
            problems.append(f"Closing day '{d}' appears more than once in the registry.")

        buffer_types = [node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE"
                        for node in self.all_nodes()
                        if node_kind(node) == "Buffer" and node_uid(node) not in disabled]
        exit_count = buffer_types.count("EXIT")
        if exit_count == 0:
            problems.append("No EXIT buffer: the parser expects exactly one to define the simulation's exit.")
        elif exit_count > 1:
            problems.append(f"{exit_count} EXIT buffers: the simulation allows at most one.")


        gen_count = sum(1 for n in self.all_nodes()
                        if node_kind(n) == "PieceGenerator" and node_uid(n) not in disabled)
        if gen_count == 0:
            problems.append("No piece generator: the simulation requires exactly one.")
        elif gen_count > 1:
            problems.append(f"{gen_count} piece generators: the simulation allows exactly one.")


        if start_dt is None:
            problems.append("Simulation start date missing or not 'dd-mm-yyyy hh:mm' "
                            "(Simulation > Settings...).")


        crit = self.stopping_criterion or {}
        gen_node = next((n for n in self.all_nodes()
                         if node_kind(n) == "PieceGenerator" and node_uid(n) not in disabled), None)
        if not crit:
            problems.append("No stopping criterion set (Simulation > Settings...); "
                            "the simulation may never terminate.")
        elif crit.get("type") == "ByPiecesProduced":
            if exit_count != 1:
                problems.append("Stopping criterion 'By pieces produced' needs exactly one EXIT buffer to count.")
            goals = crit.get("models_goals", [])
            if not goals:
                problems.append("Stopping criterion 'By pieces produced' has no model goals (Simulation > Settings...).")
            if any(as_int(g.get("goal", 0)) < 0 for g in goals):
                problems.append("Stopping criterion 'By pieces produced': every model goal must be a non-negative integer.")
            if goals and sum(as_int(g.get("goal", 0)) for g in goals) <= 0:
                problems.append("Stopping criterion 'By pieces produced': the total goal must be positive "
                                "(give at least one model a goal above zero).")
            if as_float(crit.get("grace_period", 0.0)) < 0:
                problems.append("Stopping criterion 'By pieces produced': the grace period must be >= 0 "
                                "minutes (the loader also rejects one longer than the generator's shifts).")
            if crit.get("gap") is not None and as_float(crit.get("gap"), 0.0) <= 0:
                problems.append("Stopping criterion 'By pieces produced': the gap must be > 0 minutes "
                                "(or switch back to the automatic gap).")
            if gen_node is not None:
                self._check_flushability(gen_node, [g.get("model") for g in goals if g.get("model")],
                                         "bufs_out", "Piece generator", problems)
        elif crit.get("type") == "ByTime":
            stop_dt = parse_date_time(crit.get("time"))
            if stop_dt is None:
                problems.append("Stopping date must be 'dd-mm-yyyy hh:mm' (Simulation > Settings...).")
            elif start_dt is not None and stop_dt <= start_dt:
                problems.append("Stopping date must be after the simulation start date.")
            probs = crit.get("models_probs", [])
            if not probs:
                problems.append("Stopping criterion 'By time' has no model probabilities (Simulation > Settings...).")
            freeloaders = [mp for mp in probs if mp.get("probability") is None]
            if len(freeloaders) > 1:
                problems.append("Stopping criterion 'By time': at most one model can be the freeloader "
                                "(the one with no probability).")


            consts = [mp["probability"].get("value", 0.0) for mp in probs
                      if isinstance(mp.get("probability"), dict) and mp["probability"].get("kind") == "constant"]
            if any(not 0 <= v <= 1 for v in consts):
                problems.append("Stopping criterion 'By time': model probabilities must be in [0, 1].")
            if probs and all(mp.get("probability") is None
                             or (isinstance(mp.get("probability"), dict)
                                 and mp["probability"].get("kind") == "constant")
                             for mp in probs):
                s = sum(consts)
                if not freeloaders and abs(s - 1.0) > 1e-6:
                    problems.append(f"Stopping criterion 'By time': model probabilities sum to {s:g} "
                                    f"(must sum to 1, or mark one model as the freeloader).")
                elif freeloaders and s > 1 + 1e-6:
                    problems.append(f"Stopping criterion 'By time': model probabilities sum to {s:g} "
                                    f"(must be <= 1 so the freeloader can take the rest).")
            if not crit.get("gap"):
                problems.append("Stopping criterion 'By time' has no gap between pieces (Simulation > Settings...).")
            if gen_node is not None:
                self._check_flushability(gen_node, [mp.get("model") for mp in probs if mp.get("model")],
                                         "bufs_out", "Piece generator", problems)


        known_closing = {e.get("date") for e in self.closing_day_registry if e.get("date")}
        for s in self.shift_registry:
            sname = s.get("name", "?")
            offs = [parse_date(x) for x in s.get("days_off", [])]
            if any(o is None for o in offs):
                problems.append(f"Shift '{sname}': days off must be 'dd-mm-yyyy'.")
            for x in s.get("days_off", []):
                if x not in known_closing:
                    problems.append(f"Shift '{sname}': day off '{x}' is not in the closing-days "
                                    f"registry (Registries > Edit closing days...).")
            if s.get("mode") == "custom":
                ivs = s.get("custom_intervals", [])
                if not ivs:
                    problems.append(f"Custom shift '{sname}' has no intervals.")
                _check_date_intervals(f"Custom shift '{sname}'", ivs, start_dt, problems)
            else:
                hz = s.get("horizon", {})
                h0 = parse_date(hz.get("start"))
                h1 = parse_date(hz.get("end"))
                if h0 is None or h1 is None:
                    problems.append(f"Shift '{sname}': horizon dates must be 'dd-mm-yyyy'.")
                elif h1 < h0:
                    problems.append(f"Shift '{sname}': horizon ends before it starts.")
                elif start_dt is not None and h0.date() < start_dt.date():
                    problems.append(f"Shift '{sname}': horizon begins before the simulation start date.")
                if (h0 is not None and h1 is not None
                        and any(o is not None and not (h0 <= o <= h1) for o in offs)):
                    problems.append(f"Shift '{sname}': a day off lies outside the horizon.")

        return problems

    def delete_selected_nodes(self):
        selected_nodes = self.graph.selected_nodes()
        if not selected_nodes:
            return
        for node in selected_nodes:
            self.graph.delete_node(node)


    @staticmethod
    def _node_enabled(node) -> bool:
        try:
            return not bool(node.disabled())
        except Exception:
            return True

    def _card_json(self, node) -> dict | None:
        data = node.to_clean_json()
        if data:
            data["enabled"] = self._node_enabled(node)
        return data

    def toggle_disable_selected_cards(self, context_node=None):
        nodes = self._selected_cards(context_node)
        if not nodes:
            self.statusBar().showMessage("Nothing selected to disable/enable.")
            return

        disable = any(self._node_enabled(n) for n in nodes)
        for node in nodes:
            try:
                node.set_disabled(disable)
            except Exception:
                pass
        self.mark_dirty()
        verb = "Disabled" if disable else "Enabled"
        self.statusBar().showMessage(f"{verb} {len(nodes)} card(s).")


    CARD_CLIPBOARD_FORMAT = "flow-designer-cards"

    @staticmethod
    def _text_widget_with_focus():
        w = QtWidgets.QApplication.focusWidget()
        if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
            return w
        if isinstance(w, QtWidgets.QAbstractSpinBox):
            return w.lineEdit()
        return None

    def _selected_cards(self, context_node=None):
        nodes = [n for n in self.graph.selected_nodes() if hasattr(n, "to_clean_json")]
        if context_node is not None and context_node not in nodes:
            nodes = [context_node]
        return nodes

    def _cards_payload(self, nodes):
        cards = [c for c in (self._card_json(n) for n in nodes) if c and c.get("kind")]
        if not cards:
            return None
        uids = {c["id"] for c in cards if c.get("id")}
        conns = [c for c in self.connections_clean()
                 if c.get("from_node") in uids and c.get("to_node") in uids]
        return {"format": self.CARD_CLIPBOARD_FORMAT, "nodes": cards, "connections": conns}


    _CARD_FOOTPRINT = (240.0, 200.0)

    def _materialize_cards(self, payload, delta=0.0, center=None):
        data = self._remap_ids(payload)
        positions = [c["position"] for c in data.get("nodes", [])
                     if isinstance(c.get("position"), list) and len(c["position"]) >= 2]
        dx = dy = delta
        if center is not None and positions:
            fw, fh = self._CARD_FOOTPRINT
            cx = (min(p[0] for p in positions) + max(p[0] for p in positions) + fw) / 2.0
            cy = (min(p[1] for p in positions) + max(p[1] for p in positions) + fh) / 2.0
            dx += center[0] - cx
            dy += center[1] - cy
        for card in data.get("nodes", []):
            pos = card.get("position")
            if isinstance(pos, list) and len(pos) >= 2:
                card["position"] = [pos[0] + dx, pos[1] + dy]
        created = self._instantiate_cards(data)
        if created:
            try:
                self.graph.clear_selection()
            except Exception:
                pass
            for node in created.values():
                try:
                    node.set_selected(True)
                except Exception:
                    pass
        return created

    def copy_selected_cards(self, context_node=None):
        w = self._text_widget_with_focus()
        if w is not None:
            w.copy()
            return
        payload = self._cards_payload(self._selected_cards(context_node))
        if payload is None:
            self.statusBar().showMessage("Nothing selected to copy.")
            return
        QtWidgets.QApplication.clipboard().setText(json.dumps(payload, indent=2, ensure_ascii=False))
        self.statusBar().showMessage(f"Copied {len(payload['nodes'])} card(s).")

    def cut_selected_cards(self, context_node=None):
        w = self._text_widget_with_focus()
        if w is not None:
            w.cut()
            return
        nodes = self._selected_cards(context_node)
        payload = self._cards_payload(nodes)
        if payload is None:
            self.statusBar().showMessage("Nothing selected to cut.")
            return
        QtWidgets.QApplication.clipboard().setText(json.dumps(payload, indent=2, ensure_ascii=False))
        for node in nodes:
            self.graph.delete_node(node)
        self.statusBar().showMessage(f"Cut {len(payload['nodes'])} card(s).")

    def duplicate_selected_cards(self, context_node=None):
        payload = self._cards_payload(self._selected_cards(context_node))
        if payload is None:
            self.statusBar().showMessage("Nothing selected to duplicate.")
            return
        created = self._materialize_cards(payload, 40.0)
        self.statusBar().showMessage(f"Duplicated {len(created)} card(s).")

    def paste_cards(self):
        w = self._text_widget_with_focus()
        if w is not None:
            w.paste()
            return
        text = QtWidgets.QApplication.clipboard().text()
        try:
            payload = json.loads(text or "")
        except Exception:
            payload = None
        if not isinstance(payload, dict) or payload.get("format") != self.CARD_CLIPBOARD_FORMAT:
            self.statusBar().showMessage("Clipboard holds no copied cards.")
            return


        if getattr(self, "_last_paste_text", None) == text:
            self._paste_serial += 1
        else:
            self._last_paste_text = text
            self._paste_serial = 0
        created = self._materialize_cards(payload, 40.0 * self._paste_serial,
                                          center=self.current_view_center())
        if not created:
            self.statusBar().showMessage("Clipboard holds no pasteable cards.")
            return
        self.statusBar().showMessage(f"Pasted {len(created)} card(s).")

    def set_node_position_safe(self, node, x: float, y: float):
        try:
            node.set_pos(x, y)
        except Exception:
            try:
                node.set_x_pos(x)
                node.set_y_pos(y)
            except Exception:
                pass

    def set_json_property_safe(self, node, prop_name: str, value):
        if node.has_property(prop_name):
            node.set_property(prop_name, json.dumps(value, ensure_ascii=False))
        else:
            node.create_property(prop_name, json.dumps(value, ensure_ascii=False))

    def set_property_safe(self, node, prop_name: str, value):
        if node.has_property(prop_name):
            node.set_property(prop_name, value)
        else:
            node.create_property(prop_name, value)

    def node_type_from_kind(self, kind: str) -> str:
        mapping = {
            "Shutdowns": "simulation.flow.ShutdownsNode",
            "Buffer": "simulation.flow.BufferNode",
            "Router": "simulation.flow.RouterNode",
            "PieceGenerator": "simulation.flow.PieceGeneratorNode",
            "Task": "simulation.flow.TaskNode",
            "ResourceTask": "simulation.flow.ResourceTaskNode",
            "Breakdown": "simulation.flow.BreakdownNode",
        }
        if kind not in mapping:
            raise ValueError(f"Unknown node kind in JSON: {kind}")
        return mapping[kind]


    _IMPORT_JSON_PROPS = {
        "Shutdowns": ["intervals", "generator"],
        "Buffer": ["valid_models"],
        "PieceGenerator": ["shifts"],
        "Task": ["models_configs", "startup_duration", "loading_duration", "operators",
                 "loading_operators", "startup_operators", "task_shifts", "policies"],
        "ResourceTask": ["non_transformed_resources", "transformed_resources", "resources_out",
                         "duration", "startup_duration", "loading_duration", "operators",
                         "loading_operators", "startup_operators", "task_shifts", "policies"],
        "Breakdown": ["mtbf", "mttr"],
    }
    _IMPORT_SCALAR_PROPS = {
        "Shutdowns": ["shutdown_type", "mode"],
        "Buffer": ["buffer_type"],
        "Task": ["operator_scope", "resource_scope", "min_carriers", "max_capacity",
                 "contiguous_carriers", "independent_carriers", "timeout", "priority", "admin", "collector_type"],
        "ResourceTask": ["resource_scope", "operator_scope", "resource_collector_type",
                         "min_carriers", "max_capacity", "min_carrier_capacity", "max_carrier_capacity",
                         "contiguous_carriers", "independent_carriers", "timeout", "priority", "admin"],
    }

    def apply_clean_json_to_node(self, node, node_data: dict):
        kind = node_data.get("kind")

        if node_data.get("id"):
            self.set_property_safe(node, "uid", node_data["id"])
        if "name" in node_data:
            node.set_name(node_data["name"])
        position = node_data.get("position", [0, 0])
        if isinstance(position, list) and len(position) >= 2:
            self.set_node_position_safe(node, position[0], position[1])
        try:
            node.set_disabled(not bool(node_data.get("enabled", True)))
        except Exception:
            pass

        for key in self._IMPORT_JSON_PROPS.get(kind, []):
            if key in node_data:
                self.set_json_property_safe(node, key, node_data[key])
        for key in self._IMPORT_SCALAR_PROPS.get(kind, []):
            if key in node_data:
                self.set_property_safe(node, key, node_data[key])


        if kind == "Shutdowns" and node_data.get("shutdown_type") is not None:

            self.set_property_safe(node, "shutdown_type",
                                   sentence_case(to_canonical(node_data["shutdown_type"], SHUTDOWN_TYPES)))
        if kind == "Router":
            prob_map = {}
            for item in node_data.get("buffer_probs", []):
                bid = item.get("buffer")
                if bid:
                    prob_map[bid] = item.get("probability", {"kind": "constant", "value": 0.0})
            self.set_json_property_safe(node, "buffer_probs", prob_map)

    def _remap_ids(self, data: dict) -> dict:
        data = json.loads(json.dumps(data))
        mapping = {}
        for node in data.get("nodes", []):
            if isinstance(node, dict) and node.get("id"):
                mapping[node["id"]] = new_uid(str(node.get("kind", "node")).lower())

        def walk(obj):
            if isinstance(obj, dict):
                return {k: walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [walk(v) for v in obj]
            if isinstance(obj, str) and obj in mapping:
                return mapping[obj]
            return obj
        return walk(data)

    def _resolve_ref_ids_to_names(self, data: dict) -> dict:
        data = json.loads(json.dumps(data))
        regs = {"model": data.get("models", []), "resource": data.get("resources", []),
                "operator": data.get("operators", []), "shift": data.get("shifts", [])}
        id_to_name = {k: {e["id"]: e["name"] for e in v if e.get("id") and e.get("name")}
                      for k, v in regs.items()}

        id_to_name["closing_day"] = {e["id"]: e["date"] for e in data.get("closing_days", [])
                                     if e.get("id") and e.get("date")}
        _apply_ref_map(data.get("nodes", []), regs["model"], regs["resource"], regs["operator"],
                       lambda kind, v: id_to_name[kind].get(v, v), shifts=regs["shift"],
                       criterion=data.get("stopping_criterion"))
        return data

    def _adopt_orphan_days_off(self) -> None:
        known = {e.get("date") for e in self.closing_day_registry if e.get("date")}
        for s in self.shift_registry:
            for d in s.get("days_off", []):
                if d not in known and parse_date(d) is not None:
                    self.closing_day_registry.append({"date": d, "name": ""})
                    known.add(d)
        self.closing_day_registry.sort(
            key=lambda e: (parse_date(e.get("date")) or datetime.max, e.get("date", "")))

    def _instantiate_cards(self, data: dict) -> dict:
        id_to_node = {}
        for card in data.get("nodes", []):
            if not isinstance(card, dict):
                continue
            kind = card.get("kind")
            if not kind:
                continue
            try:
                node_type = self.node_type_from_kind(kind)
            except Exception as error:
                print(f"[WARNING] Skipping unknown node kind {kind}: {error}")
                continue
            node = self.graph.create_node(node_type)
            self.apply_clean_json_to_node(node, card)
            if card.get("id"):
                id_to_node[card["id"]] = node

        for connection in data.get("connections", []):
            from_node = id_to_node.get(connection.get("from_node"))
            to_node = id_to_node.get(connection.get("to_node"))
            if from_node is None or to_node is None:
                continue
            try:
                connect_ports_by_name(from_node, connection.get("from_port"), to_node, connection.get("to_port"))
            except Exception as error:
                print("[WARNING] Could not reconnect "
                      f"{connection.get('from_node')}.{connection.get('from_port')} -> "
                      f"{connection.get('to_node')}.{connection.get('to_port')}: {error}")
        return id_to_node

    def load_clean_json(self, data: dict):
        data = self._resolve_ref_ids_to_names(data)
        self.model_registry = [{"name": m["name"], "parent": m.get("parent") or None, "id": m.get("id")}
                               for m in data.get("models", []) if m.get("name")]
        self.resource_registry = [dict(e) for e in data.get("resources", []) if e.get("name")]
        self.operator_registry = [dict(e) for e in data.get("operators", []) if e.get("name")]
        self.closing_day_registry = [dict(e) for e in data.get("closing_days", []) if e.get("date")]
        self.shift_registry = [dict(e) for e in data.get("shifts", []) if e.get("name")]
        self._adopt_orphan_days_off()
        ensure_ids(self.model_registry, "model")
        ensure_ids(self.resource_registry, "resource")
        ensure_ids(self.operator_registry, "operator")
        ensure_ids(self.shift_registry, "shift")
        ensure_ids(self.closing_day_registry, "closingday", key="date")
        if data.get("stopping_criterion"):
            self.stopping_criterion = data["stopping_criterion"]
        if data.get("start_date"):
            self.start_date = data["start_date"]
        if data.get("seed") is not None:
            self.seed = int(data["seed"])
        self._instantiate_cards(data)
        self.frame_all()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app._height_capper = _DialogHeightCapper()
    app.installEventFilter(app._height_capper)
    window = FlowEditorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
