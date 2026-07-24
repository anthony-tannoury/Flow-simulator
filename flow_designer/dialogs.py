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
    from .ui_helpers import ASSOCIATION_TYPES, BUFFER_TYPES, COLLECTOR_TYPES, PIECE_POLICY_OPTIONS, PY_DATE_TIME_FORMAT, RESOURCE_COLLECTOR_TYPES, SHUTDOWN_TYPES, STOPPING_CRITERION_TYPES, WEEKDAYS, _clear_layout, _leaf_model_names, _names, as_float, as_int, connected_nodes_from_port, fill_combo, get_property_json, node_uid, parse_date, parse_date_time, qmessage, sentence_case, set_property_json, translate_shift_entry
except ImportError:
    from ui_helpers import ASSOCIATION_TYPES, BUFFER_TYPES, COLLECTOR_TYPES, PIECE_POLICY_OPTIONS, PY_DATE_TIME_FORMAT, RESOURCE_COLLECTOR_TYPES, SHUTDOWN_TYPES, STOPPING_CRITERION_TYPES, WEEKDAYS, _clear_layout, _leaf_model_names, _names, as_float, as_int, connected_nodes_from_port, fill_combo, get_property_json, node_uid, parse_date, parse_date_time, qmessage, sentence_case, set_property_json, translate_shift_entry

try:
    from .widgets import AlternativesWidget, ClosingDayPickerWidget, CustomIntervalListWidget, DateTimeWidget, DateWidget, FixedGoalsWidget, FixedModelProbsWidget, InfFloatWidget, ModelConfigsWidget, ModelTreeWidget, PoliciesWidget, ResourcePickerWidget, SamplerWidget, ShiftPickerWidget, TimeFunctionWidget, _DayRow, _OutputsWidget, _TransformedWidget
except ImportError:
    from widgets import AlternativesWidget, ClosingDayPickerWidget, CustomIntervalListWidget, DateTimeWidget, DateWidget, FixedGoalsWidget, FixedModelProbsWidget, InfFloatWidget, ModelConfigsWidget, ModelTreeWidget, PoliciesWidget, ResourcePickerWidget, SamplerWidget, ShiftPickerWidget, TimeFunctionWidget, _DayRow, _OutputsWidget, _TransformedWidget


class ModelRegistryDialog(QtWidgets.QDialog):
    def __init__(self, parent, models: List[dict]):
        super().__init__(parent)
        self.setWindowTitle("Edit models")
        self.resize(520, 360)

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Model name", "Parent model"])
        self.table.horizontalHeader().setStretchLastSection(True)

        btn_add = QtWidgets.QPushButton("Add")
        btn_remove = QtWidgets.QPushButton("Remove selected")
        btn_ok = QtWidgets.QPushButton("OK")
        btn_cancel = QtWidgets.QPushButton("Cancel")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(btn_add)
        top.addWidget(btn_remove)
        top.addStretch()

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(btn_ok)
        bottom.addWidget(btn_cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Define models once, then use pickers in cards."))
        layout.addWidget(self.table)
        layout.addLayout(top)
        layout.addLayout(bottom)

        btn_add.clicked.connect(self.add_row)
        btn_remove.clicked.connect(self.remove_selected)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        for m in models:
            self.add_row(m.get("name", ""), m.get("parent", ""))

    def add_row(self, name="", parent=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(parent or ""))

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def models(self) -> List[dict]:
        result = []
        seen = set()
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, 0)
            parent_item = self.table.item(r, 1)
            name = name_item.text().strip() if name_item else ""
            parent = parent_item.text().strip() if parent_item else ""
            if not name:
                continue
            if name in seen:
                raise ValueError(f"Duplicate model name: {name}")
            seen.add(name)
            result.append({"name": name, "parent": parent or None})

        valid = {m["name"] for m in result}
        for m in result:
            if m["parent"] and m["parent"] not in valid:
                raise ValueError(f"Parent model '{m['parent']}' is not defined.")
        return result

    def accept(self):
        try:
            self.models()
        except Exception as e:
            qmessage(self, "Invalid models", str(e), QtWidgets.QMessageBox.Warning)
            return
        super().accept()


class ShiftEditorDialog(QtWidgets.QDialog):

    SHIFT_MODES = [("Weekly", "weekly"), ("Custom", "custom")]

    def __init__(self, parent=None, entry=None, closing_days=None):
        super().__init__(parent)
        self.setWindowTitle("Shift definition")
        entry = entry or {}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(entry.get("name", ""))
        form.addRow("Name", self.name)
        self.mode = QtWidgets.QComboBox()
        for label, canonical in self.SHIFT_MODES:
            self.mode.addItem(label, canonical)
        form.addRow("Type", self.mode)
        lay.addLayout(form)
        self._stack = QtWidgets.QStackedWidget()
        lay.addWidget(self._stack)


        weekly = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(weekly)
        wl.addWidget(QtWidgets.QLabel("Shifts per weekday (times of day as hours + minutes):"))
        days = entry.get("days", [])
        self.day_rows = []
        for i, label in enumerate(WEEKDAYS):
            d = days[i] if i < len(days) else {}
            row = _DayRow(label, d.get("working", False), d.get("intervals"))
            self.day_rows.append(row)
            wl.addWidget(row)
        form2 = QtWidgets.QFormLayout()
        hz = entry.get("horizon", {})
        hbox = QtWidgets.QHBoxLayout()
        self.h_start = DateWidget(hz.get("start"))
        self.h_end = DateWidget(hz.get("end"))
        hbox.addWidget(QtWidgets.QLabel("From day:")); hbox.addWidget(self.h_start)
        hbox.addWidget(QtWidgets.QLabel("To day:")); hbox.addWidget(self.h_end); hbox.addStretch(1)
        hw = QtWidgets.QWidget(); hw.setLayout(hbox)
        form2.addRow("Horizon", hw)
        wl.addLayout(form2)
        self._stack.addWidget(weekly)


        custom = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(custom)
        cl.addWidget(QtWidgets.QLabel("Absolute intervals (dd-mm-yyyy hh:mm). They are converted to\n"
                                      "minutes relative to the simulation start date (Simulation > Settings...)."))
        self.custom = CustomIntervalListWidget(entry.get("custom_intervals", []))
        cl.addWidget(self.custom)
        cl.addStretch(1)
        self._stack.addWidget(custom)

        self.mode.currentIndexChanged.connect(self._stack.setCurrentIndex)
        mi = self.mode.findData(entry.get("mode", "weekly"))
        self.mode.setCurrentIndex(mi if mi >= 0 else 0)
        self._stack.setCurrentIndex(self.mode.currentIndex())


        closing_days = closing_days or []
        if closing_days:
            lay.addWidget(QtWidgets.QLabel("Days off (check closing days; applies in either mode):"))
        else:
            lay.addWidget(QtWidgets.QLabel("Days off: the closing-days registry is empty\n"
                                           "(add days in Registries > Edit closing days...)."))
        self.days_off = ClosingDayPickerWidget(closing_days, chosen=entry.get("days_off", []))
        self.days_off.setMaximumHeight(140)
        lay.addWidget(self.days_off)


        rep = entry.get("repeat") or {}
        rep_box = QtWidgets.QGroupBox("Repeat (duplicate this shift, each copy shifted later)")
        rf = QtWidgets.QFormLayout(rep_box)
        self.rep_count = QtWidgets.QLineEdit(str(int(rep.get("count", 0))))
        self.rep_count.setValidator(QtGui.QIntValidator(0, 100000, self))
        self.rep_count.setMaximumWidth(80)
        rf.addRow("Repetitions (extra copies)", self.rep_count)
        trans = QtWidgets.QHBoxLayout()
        self.rep_y = QtWidgets.QLineEdit(str(int(rep.get("years", 0))))
        self.rep_mo = QtWidgets.QLineEdit(str(int(rep.get("months", 0))))
        self.rep_w = QtWidgets.QLineEdit(str(int(rep.get("weeks", 0))))
        self.rep_d = QtWidgets.QLineEdit(str(int(rep.get("days", 0))))
        for w, unit in ((self.rep_y, "yr"), (self.rep_mo, "mo"), (self.rep_w, "wk"), (self.rep_d, "d")):
            w.setValidator(QtGui.QIntValidator(0, 100000, self)); w.setMaximumWidth(56)
            trans.addWidget(w); trans.addWidget(QtWidgets.QLabel(unit))
        trans.addStretch(1)
        tw = QtWidgets.QWidget(); tw.setLayout(trans)
        rf.addRow("Translation (each copy is this much later)", tw)
        rf.addRow("", QtWidgets.QLabel(
            "Years and months are calendar-aware: +1 yr is the same date next year\n"
            "(leap years handled). Each copy's days off are the ones you pick here,\n"
            "shifted into that copy's period."))
        lay.addWidget(rep_box)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def data(self):
        out = {
            "name": self.name.text().strip(),
            "mode": self.mode.currentData(),
            "days": [r.data() for r in self.day_rows],
            "days_off": self.days_off.value(),
            "horizon": {"start": self.h_start.get_value(), "end": self.h_end.get_value()},
            "custom_intervals": self.custom.value(),
        }
        count = as_int(self.rep_count.text())
        repeat = {"count": count, "years": as_int(self.rep_y.text()), "months": as_int(self.rep_mo.text()),
                  "weeks": as_int(self.rep_w.text()), "days": as_int(self.rep_d.text())}
        if count > 0 and any(repeat[k] for k in ("years", "months", "weeks", "days")):
            out["repeat"] = repeat
        return out

    def accept(self):


        count = as_int(self.rep_count.text())
        translation = any(as_int(w.text()) for w in (self.rep_y, self.rep_mo, self.rep_w, self.rep_d))
        if count > 0 and not translation:
            qmessage(self, "Repeat has no translation",
                     "You set repetitions but the translation is zero, so the copies "
                     "would land on top of the original.\nGive the copies an offset "
                     "(e.g. 1 yr for a yearly repeat), or set repetitions back to 0.",
                     QtWidgets.QMessageBox.Warning)
            return
        super().accept()


class TranslateShiftDialog(QtWidgets.QDialog):

    def __init__(self, parent, entries):
        super().__init__(parent)
        self.setWindowTitle("New shift from existing (translated)")
        self._entries = entries
        self._name_auto = True
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.source = QtWidgets.QComboBox()
        for i, entry in enumerate(entries):
            self.source.addItem(entry.get("name") or f"(shift {i + 1})", i)
        form.addRow("Copy from", self.source)

        self.direction = QtWidgets.QComboBox()
        self.direction.addItem("Later (+)", 1)
        self.direction.addItem("Earlier (−)", -1)
        form.addRow("Direction", self.direction)

        dur = QtWidgets.QHBoxLayout()
        self.days = QtWidgets.QLineEdit("0")
        self.hours = QtWidgets.QLineEdit("8")
        self.minutes = QtWidgets.QLineEdit("0")
        for w, unit in ((self.days, "d"), (self.hours, "h"), (self.minutes, "m")):
            w.setMaximumWidth(56)
            w.setValidator(QtGui.QIntValidator(0, 100000, self))
            dur.addWidget(w); dur.addWidget(QtWidgets.QLabel(unit))
        dur.addStretch(1)
        dw = QtWidgets.QWidget(); dw.setLayout(dur)
        form.addRow("Translate by", dw)

        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("New shift name")
        self.name.textEdited.connect(lambda *_: setattr(self, "_name_auto", False))
        form.addRow("Name", self.name)
        lay.addLayout(form)

        for w in (self.days, self.hours, self.minutes):
            w.textChanged.connect(self._suggest_name)
        self.source.currentIndexChanged.connect(self._suggest_name)
        self.direction.currentIndexChanged.connect(self._suggest_name)
        self._suggest_name()

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def total_minutes(self):
        total = (as_int(self.days.text()) * 1440 + as_int(self.hours.text()) * 60
                 + as_int(self.minutes.text()))
        return self.direction.currentData() * total

    def _duration_tag(self):
        m = self.total_minutes()
        d, rem = divmod(abs(m), 1440)
        h, mn = divmod(rem, 60)
        parts = [f"{d}d"] * bool(d) + [f"{h}h"] * bool(h) + [f"{mn}m"] * bool(mn)
        return ("+" if m >= 0 else "−") + ("".join(parts) or "0")

    def _suggest_name(self, *_):
        if not self._entries or not self._name_auto:
            return
        src = self._entries[self.source.currentData()].get("name") or "shift"
        self.name.setText(f"{src} {self._duration_tag()}")

    def result_entry(self):
        src = self._entries[self.source.currentData()]
        name = self.name.text().strip() or f"{src.get('name', 'shift')} {self._duration_tag()}"
        entry = translate_shift_entry(src, self.total_minutes(), name=name)
        entry.pop("id", None)
        return entry


class OperatorEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, entry=None, shift_names=None):
        super().__init__(parent)
        self.setWindowTitle("Operator group")
        entry = entry or {}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(entry.get("name", ""))
        self.capacity = QtWidgets.QLineEdit(str(entry.get("capacity", 1)))
        form.addRow("Name", self.name)
        form.addRow("Capacity (number of operators)", self.capacity)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("Productivity:"))
        self.prod = SamplerWidget(entry.get("productivity"))
        lay.addWidget(self.prod)
        lay.addWidget(QtWidgets.QLabel("Shifts (their concatenation is the group's schedule):"))
        self.shifts = QtWidgets.QListWidget()
        chosen = set(entry.get("shifts", []))
        for nm in (shift_names or []):
            it = QtWidgets.QListWidgetItem(nm)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if nm in chosen else QtCore.Qt.Unchecked)
            self.shifts.addItem(it)
        lay.addWidget(self.shifts)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def data(self):
        shifts = [self.shifts.item(i).text() for i in range(self.shifts.count())
                  if self.shifts.item(i).checkState() == QtCore.Qt.Checked]
        return {
            "name": self.name.text().strip(),
            "capacity": as_int(self.capacity.text(), 1),
            "productivity": self.prod.get_value(),
            "shifts": shifts,
        }


class ResourceEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, entry=None):
        super().__init__(parent)
        self.setWindowTitle("Resource")
        entry = entry or {}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(entry.get("name", ""))
        self.lifespan = InfFloatWidget(entry.get("lifespan", "inf"))
        self.max_cap = QtWidgets.QLineEdit(str(entry.get("max_capacity", 1.0)))
        self.init_cap = QtWidgets.QLineEdit(str(entry.get("initial_capacity", entry.get("max_capacity", 1.0))))
        self.restockable = QtWidgets.QCheckBox("Restockable")
        form.addRow("Name", self.name)
        form.addRow("Lifespan", self.lifespan)
        form.addRow("Max storage capacity", self.max_cap)
        form.addRow("Initial capacity (in [0, max])", self.init_cap)
        form.addRow("", self.restockable)
        lay.addLayout(form)
        self.restock_box = QtWidgets.QGroupBox("Restocking")
        rlay = QtWidgets.QVBoxLayout(self.restock_box)
        rlay.addWidget(QtWidgets.QLabel("Order duration:"))
        self.order = SamplerWidget(entry.get("order_duration"))
        rlay.addWidget(self.order)
        rlay.addWidget(QtWidgets.QLabel("Delivery duration:"))
        self.delivery = SamplerWidget(entry.get("delivery_duration"))
        rlay.addWidget(self.delivery)
        tform = QtWidgets.QFormLayout()
        self.threshold = QtWidgets.QLineEdit(str(entry.get("threshold", 0.0)))
        tform.addRow("Reorder threshold", self.threshold)
        rlay.addLayout(tform)
        lay.addWidget(self.restock_box)
        self.restockable.toggled.connect(self.restock_box.setVisible)
        self.restockable.setChecked(bool(entry.get("restockable", False)))
        self.restock_box.setVisible(self.restockable.isChecked())
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def data(self):
        out = {
            "name": self.name.text().strip(),
            "restockable": self.restockable.isChecked(),
            "lifespan": self.lifespan.get_value(),
            "max_capacity": as_float(self.max_cap.text()),
            "initial_capacity": as_float(self.init_cap.text()),
        }
        if self.restockable.isChecked():
            out["order_duration"] = self.order.get_value()
            out["delivery_duration"] = self.delivery.get_value()
            out["threshold"] = as_float(self.threshold.text())
        return out


class _RegistryDialog(QtWidgets.QDialog):
    reg_title = "Registry"

    def __init__(self, parent=None, entries=None):
        super().__init__(parent)
        self.setWindowTitle(self.reg_title)
        self.resize(560, 420)
        self._entries = [dict(e) for e in (entries or [])]
        lay = QtWidgets.QVBoxLayout(self)
        self.listw = QtWidgets.QListWidget()
        self.listw.itemDoubleClicked.connect(lambda *_: self._edit())
        lay.addWidget(self.listw)
        btns = QtWidgets.QHBoxLayout()
        for label, cb in [("Add", self._add), ("Edit", self._edit), ("Remove", self._remove)]:
            b = QtWidgets.QPushButton(label); b.clicked.connect(cb); btns.addWidget(b)
        lay.addLayout(btns)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._refresh()

    def _refresh(self):
        self.listw.clear()
        for e in self._entries:
            self.listw.addItem(e.get("name", "(unnamed)"))

    def _make_editor(self, entry):
        raise NotImplementedError

    def _add(self):
        dlg = self._make_editor(None)
        if dlg.exec():
            self._entries.append(dlg.data()); self._refresh()

    def _edit(self):
        row = self.listw.currentRow()
        if row < 0:
            return
        dlg = self._make_editor(self._entries[row])
        if dlg.exec():
            self._entries[row] = dlg.data(); self._refresh()

    def _remove(self):
        row = self.listw.currentRow()
        if row >= 0:
            del self._entries[row]; self._refresh()

    def entries(self):
        return self._entries


class ResourceRegistryDialog(_RegistryDialog):
    reg_title = "Resources"

    def _make_editor(self, entry):
        return ResourceEditorDialog(self, entry)


class ShiftRegistryDialog(_RegistryDialog):
    reg_title = "Shifts"

    def __init__(self, parent=None, entries=None, closing_days=None):
        self._closing_days = closing_days or []
        super().__init__(parent, entries)

        btn = QtWidgets.QPushButton("New from existing (translated)…")
        btn.clicked.connect(self._add_translated)
        self.layout().insertWidget(self.layout().count() - 1, btn)

    def _make_editor(self, entry):
        return ShiftEditorDialog(self, entry, closing_days=self._closing_days)

    def _add_translated(self):
        if not self._entries:
            qmessage(self, "No shifts yet",
                     "Add a shift first, then you can create a translated copy of it.",
                     QtWidgets.QMessageBox.Information)
            return
        dlg = TranslateShiftDialog(self, self._entries)
        if dlg.exec():
            self._entries.append(dlg.result_entry())
            self._refresh()
            self.listw.setCurrentRow(len(self._entries) - 1)


class ClosingDaysRegistryDialog(QtWidgets.QDialog):

    def __init__(self, parent=None, entries=None):
        super().__init__(parent)
        self.setWindowTitle("Closing days")
        self.resize(480, 420)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Days the factory is closed. Shifts pick their days off from this list."))
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
        self._host = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._host)
        self._vl.setContentsMargins(0, 0, 0, 0)
        self._vl.addStretch(1)
        scroll.setWidget(self._host)
        lay.addWidget(scroll)
        add = QtWidgets.QPushButton("+ Closing day")
        add.clicked.connect(lambda: self._add())
        lay.addWidget(add)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._rows = []
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = dict(entry or {})
        row = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
        picker = DateWidget(entry.get("date") or "01-01-2026")
        label = QtWidgets.QLineEdit(entry.get("name", ""))
        label.setPlaceholderText("Label (optional)")
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        rl.addWidget(picker); rl.addWidget(label, 1); rl.addWidget(rm)
        rec = (row, picker, label, entry)
        rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec)
        self._vl.insertWidget(self._vl.count() - 1, row)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def entries(self):
        out = []
        for _, picker, label, entry in self._rows:
            e = dict(entry)
            e["date"] = picker.get_value()
            e["name"] = label.text().strip()
            out.append(e)
        out.sort(key=lambda e: (parse_date(e.get("date")) or datetime.max, e.get("date", "")))
        return out


class OperatorRegistryDialog(_RegistryDialog):
    reg_title = "Operators"

    def __init__(self, parent=None, entries=None, shift_names=None):
        self._shift_names = shift_names or []
        super().__init__(parent, entries)

    def _make_editor(self, entry):
        return OperatorEditorDialog(self, entry, self._shift_names)


class ShutdownsMenuDialog(QtWidgets.QDialog):

    MODES = [("Custom", "custom"), ("Generator", "generator")]

    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Shutdowns")
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.type = QtWidgets.QComboBox()
        fill_combo(self.type, SHUTDOWN_TYPES,
                   node.get_property("shutdown_type") if node.has_property("shutdown_type") else "NON_FLEXIBLE")
        form.addRow("Type", self.type)
        self.mode = QtWidgets.QComboBox()
        for label, canonical in self.MODES:
            self.mode.addItem(label, canonical)
        form.addRow("Intervals", self.mode)
        lay.addLayout(form)
        self._stack = QtWidgets.QStackedWidget()
        lay.addWidget(self._stack)


        custom = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(custom); cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QtWidgets.QLabel("Intervals (absolute dates, dd-mm-yyyy hh:mm):"))
        self.intervals = CustomIntervalListWidget(get_property_json(node, "intervals", []))
        cl.addWidget(self.intervals)
        cl.addStretch(1)
        self._stack.addWidget(custom)


        gen = QtWidgets.QWidget()
        gl = QtWidgets.QVBoxLayout(gen); gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(QtWidgets.QLabel("Periodic: a shutdown every 'in between' minutes, placed inside\n"
                                      "the attached task's shifts (parser calls the shutdown generator)."))
        gform = QtWidgets.QFormLayout()
        g = get_property_json(node, "generator", {})
        default_start = g.get("start") or getattr(parent, "start_date", "")
        self.g_in_between = QtWidgets.QLineEdit(str(g.get("in_between", 480.0)))
        self.g_duration = QtWidgets.QLineEdit(str(g.get("duration", 30.0)))
        self.g_start = DateTimeWidget(default_start)
        self.g_end = DateTimeWidget(g.get("end", ""))
        gform.addRow("In between (minutes)", self.g_in_between)
        gform.addRow("Duration (minutes)", self.g_duration)
        gform.addRow("From", self.g_start)
        gform.addRow("To", self.g_end)
        gl.addLayout(gform)
        gl.addStretch(1)
        self._stack.addWidget(gen)

        self.mode.currentIndexChanged.connect(self._stack.setCurrentIndex)
        mi = self.mode.findData(node.get_property("mode") if node.has_property("mode") else "custom")
        self.mode.setCurrentIndex(mi if mi >= 0 else 0)
        self._stack.setCurrentIndex(self.mode.currentIndex())

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):

        self.node.set_property("shutdown_type", sentence_case(self.type.currentData()))
        self.node.set_property("mode", self.mode.currentData())
        set_property_json(self.node, "intervals", self.intervals.value())
        set_property_json(self.node, "generator", {
            "in_between": as_float(self.g_in_between.text()),
            "duration": as_float(self.g_duration.text()),
            "start": self.g_start.get_value(),
            "end": self.g_end.get_value(),
        })


class BufferMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, model_registry):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Buffer")
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        lay.addWidget(tabs)


        buf_tab = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(buf_tab)
        bl.addWidget(QtWidgets.QLabel("Valid models (selecting a model selects its children):"))
        self.models = ModelTreeWidget(model_registry, checked=get_property_json(node, "valid_models", []))
        bl.addWidget(self.models)
        form = QtWidgets.QFormLayout()
        self.buffer_type = QtWidgets.QComboBox()
        fill_combo(self.buffer_type, BUFFER_TYPES,
                   node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE")
        form.addRow("Type", self.buffer_type)
        bl.addLayout(form)
        tabs.addTab(buf_tab, "Buffer")

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "valid_models", self.models.checked_models())
        self.node.set_property("buffer_type", self.buffer_type.currentData())


class RouterMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Router probabilities")
        lay = QtWidgets.QVBoxLayout(self)
        self._buffers = connected_nodes_from_port(node, "to_buffers", "output")
        current = get_property_json(node, "buffer_probs", {})
        self._widgets = {}
        if not self._buffers:
            lay.addWidget(QtWidgets.QLabel("Wire this router's 'to_buffers' output into buffers first."))

        self.freeloader = QtWidgets.QComboBox()
        self.freeloader.addItem("(none)", None)
        for b in self._buffers:
            self.freeloader.addItem(b.name(), node_uid(b))
        free_bid = next((bid for bid, v in current.items() if v is None), None)
        fi = self.freeloader.findData(free_bid) if free_bid else 0
        self.freeloader.setCurrentIndex(fi if fi >= 0 else 0)
        if self._buffers:
            ff = QtWidgets.QFormLayout()
            ff.addRow("Freeloader", self.freeloader)
            lay.addLayout(ff)
        form = QtWidgets.QFormLayout()
        for b in self._buffers:
            bid = node_uid(b)
            tf = TimeFunctionWidget(current.get(bid) or {"kind": "constant", "value": 0.0})
            self._widgets[bid] = tf
            form.addRow(b.name(), tf)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("(probabilities are checked to sum to 1 when sampled)"))
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)
        self.freeloader.currentIndexChanged.connect(self._sync_freeloader)
        self._sync_freeloader()

    def _sync_freeloader(self, *_):
        free_bid = self.freeloader.currentData()
        for bid, w in self._widgets.items():
            w.setDisabled(bid == free_bid)

    def apply(self):
        free_bid = self.freeloader.currentData()
        set_property_json(self.node, "buffer_probs",
                          {bid: (None if bid == free_bid else w.get_value())
                           for bid, w in self._widgets.items()})


class GeneratorMenuDialog(QtWidgets.QDialog):

    def __init__(self, parent, node, shift_names):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Piece generator")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Shifts (when the generator emits pieces):"))
        self.shifts = ShiftPickerWidget(shift_names, get_property_json(node, "shifts", []))
        lay.addWidget(self.shifts)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "shifts", self.shifts.chosen())


class SimulationSettingsDialog(QtWidgets.QDialog):

    def __init__(self, parent, start_date, criterion, model_registry, seed=0):
        super().__init__(parent)
        self.setWindowTitle("Simulation settings")
        self._leaf_models = _leaf_model_names(model_registry)
        self._pending = criterion or {}
        lay = QtWidgets.QVBoxLayout(self)

        start_box = QtWidgets.QGroupBox("Start date (calendar anchor of t=0)")
        sl = QtWidgets.QHBoxLayout(start_box)
        self.start_date = DateTimeWidget(start_date or "01-01-2026 00:00")
        sl.addWidget(self.start_date); sl.addStretch(1)
        lay.addWidget(start_box)

        seed_box = QtWidgets.QGroupBox("Random seed (the same seed reproduces the same run)")
        sdl = QtWidgets.QHBoxLayout(seed_box)
        self.seed_edit = QtWidgets.QLineEdit(str(int(seed) if seed is not None else 0))
        self.seed_edit.setValidator(QtGui.QIntValidator(0, 2**31 - 1, self))
        self.seed_edit.setMaximumWidth(120)
        sdl.addWidget(self.seed_edit); sdl.addStretch(1)
        lay.addWidget(seed_box)

        crit_box = QtWidgets.QGroupBox("Stopping criterion and piece generation")
        cl = QtWidgets.QVBoxLayout(crit_box)
        top = QtWidgets.QFormLayout()
        self.type = QtWidgets.QComboBox()
        fill_combo(self.type, STOPPING_CRITERION_TYPES)
        top.addRow("Stop on", self.type)
        cl.addLayout(top)
        self._host = QtWidgets.QWidget()
        self._host_lay = QtWidgets.QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(12, 4, 0, 0)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self._host)
        cl.addWidget(scroll)
        lay.addWidget(crit_box, 1)
        self._widgets = {}
        self.type.currentIndexChanged.connect(self._rebuild)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

        ci = self.type.findData(self._pending.get("type", "ByPiecesProduced"))
        blocked = self.type.blockSignals(True)
        self.type.setCurrentIndex(ci if ci >= 0 else 0)
        self.type.blockSignals(blocked)
        self._rebuild()

    def start_value(self):
        return self.start_date.get_value()

    def seed_value(self):
        text = self.seed_edit.text().strip()
        try:
            return int(text)
        except (ValueError, TypeError):
            return 0

    def _rebuild(self, *_):
        _clear_layout(self._host_lay)
        self._widgets = {}
        canonical = self.type.currentData()
        src = self._pending if self._pending.get("type") == canonical else {}
        if canonical == "ByPiecesProduced":
            self._build_pieces(src)
        elif canonical == "ByTime":
            self._build_time(src)
        self._host_lay.addStretch(1)

    def _add_row(self, label, widget):
        box = QtWidgets.QGroupBox(label)
        bl = QtWidgets.QVBoxLayout(box); bl.setContentsMargins(8, 4, 8, 8)
        bl.addWidget(widget)
        self._host_lay.addWidget(box)

    def _build_pieces(self, src):
        goals = FixedGoalsWidget(self._leaf_models, src.get("models_goals", []))
        self._widgets["goals"] = goals
        self._add_row("Model goals (one goal per leaf model)", goals)


        pacing = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pacing)
        pl.setContentsMargins(0, 0, 0, 0)
        auto = QtWidgets.QCheckBox("Automatic (the goals are paced to fill the generator's shifts)")
        auto.setChecked(src.get("gap") is None)
        pl.addWidget(auto)
        grace = QtWidgets.QLineEdit(str(src.get("grace_period", 0.0)))
        grace.setMaximumWidth(90)
        grace_row = QtWidgets.QWidget()
        gl = QtWidgets.QHBoxLayout(grace_row); gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(QtWidgets.QLabel("Grace period (minutes; shift time kept free at the end for scrap remakes):"))
        gl.addWidget(grace); gl.addStretch(1)
        pl.addWidget(grace_row)
        gap = QtWidgets.QLineEdit(str(src.get("gap", 60.0)))
        gap.setMaximumWidth(90)
        gap_row = QtWidgets.QWidget()
        gpl = QtWidgets.QHBoxLayout(gap_row); gpl.setContentsMargins(0, 0, 0, 0)
        gpl.addWidget(QtWidgets.QLabel("Gap (minutes between two pieces):"))
        gpl.addWidget(gap); gpl.addStretch(1)
        pl.addWidget(gap_row)
        self._widgets["auto_gap"] = auto
        self._widgets["grace"] = grace
        self._widgets["gap"] = gap

        def _sync_pacing(*_):
            grace_row.setVisible(auto.isChecked())
            gap_row.setVisible(not auto.isChecked())
        auto.toggled.connect(_sync_pacing)
        _sync_pacing()
        self._add_row("Gap between pieces", pacing)

        timeout = InfFloatWidget(src.get("timeout", "inf"))
        self._widgets["timeout"] = timeout
        self._add_row("Timeout (minutes)", timeout)

    def _build_time(self, src):
        stop = DateTimeWidget(src.get("time", ""))
        self._widgets["time"] = stop
        self._add_row("Stop at", stop)
        gap = TimeFunctionWidget(src.get("gap") or {"kind": "constant", "value": 1.0})
        self._widgets["gap"] = gap
        self._add_row("Gap between pieces (minutes; constant or function of time)", gap)
        probs = FixedModelProbsWidget(self._leaf_models, src.get("models_probs", []))
        self._widgets["probs"] = probs
        self._add_row("Model probabilities (one per leaf model; the freeloader gets 1 - sum of the others)", probs)

    def value(self):
        canonical = self.type.currentData()
        if canonical == "ByPiecesProduced":
            out = {"type": "ByPiecesProduced",
                   "timeout": self._widgets["timeout"].get_value()}
            if self._widgets["auto_gap"].isChecked():
                out["grace_period"] = as_float(self._widgets["grace"].text())
            else:
                out["gap"] = as_float(self._widgets["gap"].text())
            out["models_goals"] = self._widgets["goals"].value()
            return out
        return {"type": "ByTime",
                "time": self._widgets["time"].get_value(),
                "gap": self._widgets["gap"].get_value(),
                "models_probs": self._widgets["probs"].value()}


class BreakdownMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Breakdown")
        lay = QtWidgets.QVBoxLayout(self)
        mtbf = get_property_json(node, "mtbf", {}) or {}
        lay.addWidget(QtWidgets.QLabel("MTBF (mean time between failures):"))
        self.mode = QtWidgets.QComboBox()
        fill_combo(self.mode, ["distribution", "bathtub"], mtbf.get("mode", "distribution"))
        lay.addWidget(self.mode)
        self.dist = SamplerWidget(mtbf.get("distribution"))
        lay.addWidget(self.dist)
        self.bathtub_box = QtWidgets.QGroupBox("Bathtub failure-rate a·e^(t/tau)+c+(beta/eta)(t/eta)^(beta-1)")
        bl = QtWidgets.QFormLayout(self.bathtub_box)
        self.bt = {}
        for k, d in (("a", 0.001), ("tau", 500.0), ("c", 0.01), ("beta", 2.0), ("eta", 300.0),
                     ("tolerance", 60.0), ("max_iters", 10000)):
            e = QtWidgets.QLineEdit(str(mtbf.get(k, d))); self.bt[k] = e

            bl.addRow(k if len(k) <= 4 else sentence_case(k), e)
        lay.addWidget(self.bathtub_box)
        self.mode.currentTextChanged.connect(self._upd)
        lay.addWidget(QtWidgets.QLabel("MTTR (mean time to repair) distribution:"))
        self.mttr = SamplerWidget(get_property_json(node, "mttr", None))
        lay.addWidget(self.mttr)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)
        self._upd()

    def _upd(self, *_):
        bathtub = self.mode.currentData() == "bathtub"
        self.bathtub_box.setVisible(bathtub); self.dist.setVisible(not bathtub)

    def apply(self):
        if self.mode.currentData() == "distribution":
            mtbf = {"mode": "distribution", "distribution": self.dist.get_value()}
        else:
            mtbf = {"mode": "bathtub"}
            for k, e in self.bt.items():
                mtbf[k] = as_int(e.text()) if k == "max_iters" else as_float(e.text())
        set_property_json(self.node, "mtbf", mtbf)
        set_property_json(self.node, "mttr", self.mttr.get_value())


def _carrier_common_tab(node, operator_names, shift_names, collector_types, extra=None, policy_options=None):
    extra = extra or {}
    tabs = []
    acc = {}


    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("Startup duration:")); acc["startup_duration"] = SamplerWidget(get_property_json(node, "startup_duration", None)); f.addWidget(acc["startup_duration"])
    f.addWidget(QtWidgets.QLabel("Loading duration:")); acc["loading_duration"] = SamplerWidget(get_property_json(node, "loading_duration", None)); f.addWidget(acc["loading_duration"])
    for label, wdg in extra.get("durations", []):
        f.addWidget(QtWidgets.QLabel(label)); f.addWidget(wdg)
    f.addStretch(1)
    tabs.append(("Durations", _scroll(t)))


    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("Operators (alternatives):")); acc["operators"] = AlternativesWidget(operator_names, get_property_json(node, "operators", [])); f.addWidget(acc["operators"])
    f.addWidget(QtWidgets.QLabel("Loading operators:")); acc["loading_operators"] = AlternativesWidget(operator_names, get_property_json(node, "loading_operators", [])); f.addWidget(acc["loading_operators"])
    f.addWidget(QtWidgets.QLabel("Startup operators:")); acc["startup_operators"] = AlternativesWidget(operator_names, get_property_json(node, "startup_operators", [])); f.addWidget(acc["startup_operators"])
    tabs.append(("Operators", _scroll(t)))


    t = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(t)
    acc["admin"] = QtWidgets.QCheckBox(); acc["admin"].setChecked(bool(node.get_property("admin")))
    acc["admin"].setToolTip("Administrative task (inspection, waiting, storage, ...). Does not change "
                            "the simulation; the report aggregates admin vs productive tasks separately.")
    f.addRow("Admin task", acc["admin"])
    acc["min_carriers"] = QtWidgets.QLineEdit(str(node.get_property("min_carriers"))); f.addRow("Min carriers", acc["min_carriers"])
    acc["max_capacity"] = QtWidgets.QLineEdit(str(node.get_property("max_capacity"))); f.addRow("Max capacity", acc["max_capacity"])
    acc["timeout"] = InfFloatWidget(node.get_property("timeout") if node.has_property("timeout") else "inf")
    f.addRow("Timeout", acc["timeout"])
    acc["priority"] = QtWidgets.QLineEdit(str(node.get_property("priority"))); f.addRow("Priority", acc["priority"])
    acc["contiguous_carriers"] = QtWidgets.QCheckBox(); acc["contiguous_carriers"].setChecked(bool(node.get_property("contiguous_carriers"))); f.addRow("Contiguous carriers", acc["contiguous_carriers"])
    acc["independent_carriers"] = QtWidgets.QCheckBox(); acc["independent_carriers"].setChecked(bool(node.get_property("independent_carriers"))); f.addRow("Independent carriers", acc["independent_carriers"])
    for label, wdg in extra.get("carriers", []):
        f.addRow(label, wdg)
    tabs.append(("Carriers", _scroll(t)))


    t = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(t)
    acc["operator_scope"] = QtWidgets.QComboBox(); fill_combo(acc["operator_scope"], ["PER_BATCH", "PER_TASK"], node.get_property("operator_scope"))
    acc["resource_scope"] = QtWidgets.QComboBox(); fill_combo(acc["resource_scope"], ["PER_UNIT", "PER_BATCH"], node.get_property("resource_scope"))
    f.addRow("Operator scope", acc["operator_scope"]); f.addRow("Resource scope", acc["resource_scope"])
    if collector_types is not None:
        acc["collector_type"] = QtWidgets.QComboBox()
        fill_combo(acc["collector_type"], collector_types,
                   node.get_property("collector_type") if node.has_property("collector_type") else collector_types[0])
        f.addRow("Collector type", acc["collector_type"])
        acc["association_type"] = QtWidgets.QComboBox()
        acc["association_type"].setToolTip("Passive: pieces go through unchanged. Associative: the batch leaves as one "
                                           "inseparable cluster. Dissociative: incoming clusters are split back into "
                                           "their individual pieces.")
        fill_combo(acc["association_type"], ASSOCIATION_TYPES,
                   node.get_property("association_type") if node.has_property("association_type") else ASSOCIATION_TYPES[0])
        f.addRow("Association", acc["association_type"])
    for label, wdg in extra.get("scopes", []):
        f.addRow(label, wdg)
    tabs.append(("Scopes", _scroll(t)))


    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    acc["policies"] = PoliciesWidget(get_property_json(node, "policies", {}), policy_options=policy_options)
    f.addWidget(acc["policies"]); f.addStretch(1)
    tabs.append(("Protocols", _scroll(t)))


    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("Task shifts:")); acc["task_shifts"] = ShiftPickerWidget(shift_names, get_property_json(node, "task_shifts", [])); f.addWidget(acc["task_shifts"]); f.addStretch(1)
    tabs.append(("Shifts", _scroll(t)))

    return tabs, acc


def _scroll(widget):
    sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(widget); return sc


def _apply_carrier_common(node, acc):
    set_property_json(node, "startup_duration", acc["startup_duration"].get_value())
    set_property_json(node, "loading_duration", acc["loading_duration"].get_value())
    set_property_json(node, "operators", acc["operators"].get_value())
    set_property_json(node, "loading_operators", acc["loading_operators"].get_value())
    set_property_json(node, "startup_operators", acc["startup_operators"].get_value())
    node.set_property("operator_scope", acc["operator_scope"].currentData())
    node.set_property("resource_scope", acc["resource_scope"].currentData())
    if "collector_type" in acc:
        node.set_property("collector_type", acc["collector_type"].currentData())
    if "association_type" in acc:
        node.set_property("association_type", acc["association_type"].currentData())
    node.set_property("min_carriers", as_int(acc["min_carriers"].text(), 1))
    node.set_property("max_capacity", as_float(acc["max_capacity"].text(), 1.0))
    node.set_property("timeout", acc["timeout"].get_value())
    node.set_property("priority", as_int(acc["priority"].text(), 5))
    node.set_property("contiguous_carriers", acc["contiguous_carriers"].isChecked())
    node.set_property("independent_carriers", acc["independent_carriers"].isChecked())
    node.set_property("admin", acc["admin"].isChecked())
    set_property_json(node, "policies", acc["policies"].get_value())
    set_property_json(node, "task_shifts", acc["task_shifts"].chosen())


class PieceTaskMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, win):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Piece task"); self.resize(640, 640)
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs)
        t0 = QtWidgets.QWidget(); f0 = QtWidgets.QVBoxLayout(t0)
        self.models = ModelConfigsWidget(_names(win.model_registry), _names(win.resource_registry),
                                         get_property_json(node, "models_configs", []))
        f0.addWidget(self.models)
        tabs.addTab(_scroll(t0), "Models")
        common, self.acc = _carrier_common_tab(node, _names(win.operator_registry), _names(win.shift_registry), COLLECTOR_TYPES,
                                               policy_options=PIECE_POLICY_OPTIONS)
        for label, wdg in common:
            tabs.addTab(wdg, label)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "models_configs", self.models.value())
        _apply_carrier_common(self.node, self.acc)


class ResourceTaskMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, win):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Resource task"); self.resize(640, 640)
        rnames = _names(win.resource_registry)
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs)

        t0 = QtWidgets.QWidget(); f0 = QtWidgets.QVBoxLayout(t0)
        f0.addWidget(QtWidgets.QLabel("Non-transformed inputs (quantity consumed):"))
        self.non_transformed = ResourcePickerWidget(rnames, "quantity",
            [{"resource": e.get("resource"), "value": e.get("value", e.get("quantity", 1.0))} for e in get_property_json(node, "non_transformed_resources", [])])
        f0.addWidget(self.non_transformed)
        f0.addWidget(QtWidgets.QLabel("Transformed inputs (proportion + salvageable):"))
        self.transformed = _TransformedWidget(rnames, get_property_json(node, "transformed_resources", []))
        f0.addWidget(self.transformed)
        f0.addWidget(QtWidgets.QLabel("Outputs produced (bounded distribution, ≥ 0):"))
        self.outputs = _OutputsWidget(rnames, get_property_json(node, "resources_out", []))
        f0.addWidget(self.outputs)
        tabs.addTab(_scroll(t0), "Resources")

        self.duration = SamplerWidget(get_property_json(node, "duration", None))
        self.min_cc = QtWidgets.QLineEdit(str(node.get_property("min_carrier_capacity")))
        self.max_cc = QtWidgets.QLineEdit(str(node.get_property("max_carrier_capacity")))
        self.rct = QtWidgets.QComboBox(); fill_combo(self.rct, RESOURCE_COLLECTOR_TYPES, node.get_property("resource_collector_type"))
        extra = {
            "durations": [("Duration:", self.duration)],
            "carriers": [("Min carrier capacity", self.min_cc), ("Max carrier capacity", self.max_cc)],
            "scopes": [("Resource collector type", self.rct)],
        }
        common, self.acc = _carrier_common_tab(node, _names(win.operator_registry), _names(win.shift_registry), None, extra=extra)
        for label, wdg in common:
            tabs.addTab(wdg, label)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "duration", self.duration.get_value())
        set_property_json(self.node, "non_transformed_resources", self.non_transformed.entries())
        set_property_json(self.node, "transformed_resources", self.transformed.value())
        set_property_json(self.node, "resources_out", self.outputs.value())
        self.node.set_property("min_carrier_capacity", as_float(self.min_cc.text(), 1.0))
        self.node.set_property("max_carrier_capacity", as_float(self.max_cc.text(), 1.0))
        self.node.set_property("resource_collector_type", self.rct.currentData())
        _apply_carrier_common(self.node, self.acc)


class RunSimulationDialog(QtWidgets.QDialog):

    BAR_STEPS = 1000

    def __init__(self, parent, json_path: str, cpp_exe: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Run simulation")
        self.setMinimumWidth(460)
        self._json_path = json_path
        self._cpp_exe = cpp_exe
        self._meta = None
        self._sim_start = None
        self._report_dir = None
        self.view_results_requested = False
        self._error_message = None
        self._stderr_tail = []
        self._stdout_buffer = ""
        self._finished = False

        lay = QtWidgets.QVBoxLayout(self)
        file_lbl = QtWidgets.QLabel(f"Running {os.path.basename(json_path)}")
        file_lbl.setStyleSheet("font-weight: bold;")
        lay.addWidget(file_lbl)

        self._form_host = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self._form_host)
        form.setContentsMargins(0, 0, 0, 0)
        self.elapsed_lbl = QtWidgets.QLabel("0:00:00")
        form.addRow("Elapsed time", self.elapsed_lbl)
        self.sim_time_lbl = QtWidgets.QLabel("-")
        form.addRow("Simulated time", self.sim_time_lbl)
        self.pieces_lbl = QtWidgets.QLabel("-")
        self._pieces_row = form.rowCount()
        form.addRow("Pieces in exit buffer", self.pieces_lbl)
        self.timeout_lbl = QtWidgets.QLabel("-")
        self._timeout_row = form.rowCount()
        form.addRow("Timeout", self.timeout_lbl)
        self.gap_lbl = QtWidgets.QLabel("-")
        self._gap_row = form.rowCount()
        form.addRow("Piece gap", self.gap_lbl)
        lay.addWidget(self._form_host)
        self._form = form
        self._last_progress = {}
        self._outputs_phase = False
        self._set_form_row_visible(self._pieces_row, self.pieces_lbl, False)
        self._set_form_row_visible(self._timeout_row, self.timeout_lbl, False)
        self._set_form_row_visible(self._gap_row, self.gap_lbl, False)

        self.caption_lbl = QtWidgets.QLabel("")
        self.caption_lbl.setAlignment(QtCore.Qt.AlignHCenter)
        lay.addWidget(self.caption_lbl)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, self.BAR_STEPS)
        self.bar.setValue(0)
        lay.addWidget(self.bar)

        self.status_lbl = QtWidgets.QLabel("Starting...")
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        buttons = QtWidgets.QHBoxLayout()
        self.view_results_btn = QtWidgets.QPushButton("View results")
        self.view_results_btn.setVisible(False)
        self.view_results_btn.setDefault(True)
        self.view_results_btn.clicked.connect(self._on_view_results)
        self.open_report_btn = QtWidgets.QPushButton("Open report folder")
        self.open_report_btn.setVisible(False)
        self.open_report_btn.clicked.connect(self._open_report)
        buttons.addStretch(1)
        buttons.addWidget(self.view_results_btn)
        buttons.addWidget(self.open_report_btn)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self.cancel_btn)
        lay.addLayout(buttons)


        self._wall = QtCore.QElapsedTimer()
        self._wall.start()
        self._tick = QtCore.QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_elapsed)
        self._tick.start()


        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim_runner.py")
        self._proc = QtCore.QProcess(self)
        self._proc.setWorkingDirectory(repo_root)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("MPLBACKEND", "Agg")
        self._proc.setProcessEnvironment(env)
        self._proc.readyReadStandardOutput.connect(self._on_stdout)
        self._proc.readyReadStandardError.connect(self._on_stderr)
        self._proc.finished.connect(self._on_finished)


        if self._cpp_exe:
            file_lbl.setText(f"Running {os.path.basename(json_path)}  (C++ engine)")
            self._proc.start(self._cpp_exe, [json_path])
        else:
            self._proc.start(sys.executable, ["-u", runner, json_path])


    def _on_stdout(self):
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._stdout_buffer += data
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self._handle_line(line.strip())

    def _on_stderr(self):
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_tail.extend(data.splitlines())
        self._stderr_tail = self._stderr_tail[-30:]

    def _handle_line(self, line: str):
        if not line.startswith("@@"):
            return
        tag, _, payload = line[2:].partition(" ")
        try:
            info = json.loads(payload or "{}")
        except Exception:
            return
        if tag == "META":
            self._meta = info
            self._sim_start = parse_date_time(info.get("sim_start"))
            is_pieces = info.get("criterion") == "ByPiecesProduced"
            self._set_form_row_visible(self._pieces_row, self.pieces_lbl, is_pieces)
            timeout = info.get("timeout")
            if is_pieces and timeout:
                if self._sim_start is not None:
                    deadline = self._sim_start + timedelta(minutes=timeout)
                    self.timeout_lbl.setText(
                        f"{deadline.strftime(PY_DATE_TIME_FORMAT)}  (after {timeout / 1440.0:g} days)")
                else:
                    self.timeout_lbl.setText(f"{timeout:g} minutes")
                self._set_form_row_visible(self._timeout_row, self.timeout_lbl, True)

            gap = info.get("gap")
            gap_mode = info.get("gap_mode")
            if gap_mode == "function":
                self.gap_lbl.setText("function of time")
                self._set_form_row_visible(self._gap_row, self.gap_lbl, True)
            elif isinstance(gap, (int, float)):
                self.gap_lbl.setText(f"{gap:g} min between pieces  ({gap_mode})")
                self._set_form_row_visible(self._gap_row, self.gap_lbl, True)
            self.status_lbl.setText("Simulation running...")
        elif tag == "PROGRESS":
            self._show_progress(info)
        elif tag == "PHASE":
            if info.get("phase") == "outputs":
                self._enter_outputs_phase()
        elif tag == "DONE":
            self._report_dir = info.get("report_dir")
            self._show_progress(info)
        elif tag == "ERROR":
            self._error_message = info.get("message")

    def _enter_outputs_phase(self):
        if self._outputs_phase:
            return
        self._outputs_phase = True
        self._form_host.setVisible(False)
        self.caption_lbl.setText("Generating outputs")
        self.bar.setRange(0, 0)
        self.status_lbl.setText("Writing report, tables and graphs...")

    def _exit_outputs_phase(self):
        if not self._outputs_phase:
            return
        self._outputs_phase = False
        self._form_host.setVisible(True)
        self.bar.setRange(0, self.BAR_STEPS)
        self.bar.setValue(self.BAR_STEPS)
        self.caption_lbl.setText("")
        self._show_progress(self._last_progress)

    def _show_progress(self, info: dict):
        sim_now = info.get("sim_now")
        if sim_now is None:
            return
        self._last_progress = info
        if self._sim_start is not None:
            date = self._sim_start + timedelta(minutes=sim_now)
            self.sim_time_lbl.setText(f"{date.strftime(PY_DATE_TIME_FORMAT)}  (day {int(sim_now // 1440) + 1})")
        else:
            self.sim_time_lbl.setText(f"{sim_now:.0f} minutes")
        meta = self._meta or {}
        if meta.get("criterion") == "ByPiecesProduced":
            pieces = info.get("pieces")
            goal = meta.get("goal")
            if pieces is not None:
                self.pieces_lbl.setText(str(pieces))
                if goal:
                    self.caption_lbl.setText(f"{pieces} / {goal} pieces")
                    self.bar.setValue(min(self.BAR_STEPS, int(self.BAR_STEPS * pieces / goal)))
        elif meta.get("criterion") == "ByTime":
            total = meta.get("total_time")
            if total:
                self.caption_lbl.setText(f"{sim_now / 1440.0:.1f} / {total / 1440.0:.1f} days simulated")
                self.bar.setValue(min(self.BAR_STEPS, int(self.BAR_STEPS * sim_now / total)))

    def _outcome_line(self) -> str:
        meta = self._meta or {}
        if meta.get("criterion") == "ByPiecesProduced":
            pieces = self._last_progress.get("pieces")
            goal = meta.get("goal")
            if pieces is not None and goal:
                if pieces >= goal:
                    return f"Goal reached: {pieces} / {goal} pieces."
                if meta.get("timeout"):
                    return f"Timeout reached: {pieces} / {goal} pieces."
                return (f"Goal not reached: {pieces} / {goal} pieces "
                        f"(the simulation ran out of work; check the shifts).")
        elif meta.get("criterion") == "ByTime":
            return "Stop date reached."
        return "Simulation finished."

    def _on_finished(self, exit_code, *args):
        self._finished = True
        self._tick.stop()
        self._update_elapsed()
        self.cancel_btn.setText("Close")
        if exit_code == 0 and self._report_dir:
            self._render_cpp_graphs_if_needed()
            self._exit_outputs_phase()
            self.status_lbl.setText(f"{self._outcome_line()}\nReport written to:\n{self._report_dir}")
            self.open_report_btn.setVisible(True)
            self.view_results_btn.setVisible(
                os.path.isfile(os.path.join(self._report_dir, "report.json")))
        elif self._error_message:
            self._exit_outputs_phase()
            self.status_lbl.setText(f"Simulation failed: {self._error_message}")
        elif exit_code != 0:
            self._exit_outputs_phase()
            tail = "\n".join(self._stderr_tail[-8:])
            self.status_lbl.setText(f"Simulation failed (exit code {exit_code}).\n{tail}")
        else:
            self._exit_outputs_phase()
            self.status_lbl.setText(self._outcome_line())

    def _render_cpp_graphs_if_needed(self):
        if not self._cpp_exe or not self._report_dir:
            return
        if not os.path.isfile(os.path.join(self._report_dir, "graph_data.json")):
            return
        self._enter_outputs_phase()
        QtWidgets.QApplication.processEvents()
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = QtCore.QProcess(self)
        proc.setWorkingDirectory(repo_root)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("MPLBACKEND", "Agg")
        proc.setProcessEnvironment(env)
        proc.start(sys.executable, ["-m", "simulation.render_from_data", self._report_dir])
        loop = QtCore.QEventLoop(self)
        proc.finished.connect(lambda *_: loop.quit())
        QtCore.QTimer.singleShot(120000, loop.quit)
        if proc.state() != QtCore.QProcess.NotRunning:
            loop.exec()
        if proc.state() != QtCore.QProcess.NotRunning:
            proc.kill()
        if proc.exitCode() != 0:
            self._stderr_tail.extend(
                bytes(proc.readAllStandardError()).decode("utf-8", errors="replace").splitlines())
            self._stderr_tail = self._stderr_tail[-30:]


    def _set_form_row_visible(self, row: int, field_widget, visible: bool):
        try:
            self._form.setRowVisible(row, visible)
        except Exception:
            field_widget.setVisible(visible)
            lbl = self._form.labelForField(field_widget)
            if lbl is not None:
                lbl.setVisible(visible)

    def _update_elapsed(self):
        seconds = int(self._wall.elapsed() / 1000)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        self.elapsed_lbl.setText(f"{h}:{m:02d}:{s:02d}")

    def _open_report(self):
        if self._report_dir:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self._report_dir))

    def _on_view_results(self):
        self.view_results_requested = True
        self.accept()

    @property
    def report_dir(self):
        return self._report_dir

    def _confirm_abort(self) -> bool:
        answer = QtWidgets.QMessageBox.question(
            self, "Stop simulation", "The simulation is still running. Stop it?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    def _kill(self):
        try:
            self._proc.kill()
            self._proc.waitForFinished(2000)
        except Exception:
            pass

    def _on_cancel_clicked(self):
        if self._finished:
            self.accept()
        elif self._confirm_abort():
            self._kill()
            self.reject()

    def closeEvent(self, event):
        if self._finished or self._confirm_abort():
            self._kill()
            event.accept()
        else:
            event.ignore()

    def reject(self):
        if self._finished:
            super().reject()
        elif self._confirm_abort():
            self._kill()
            super().reject()
