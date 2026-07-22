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
    from .ui_helpers import DATE_FORMAT, DATE_TIME_FORMAT, DISTRIBUTION_SPECS, FUNCTION_SPECS, POLICY_OPTIONS, POLICY_TYPE_PARAMS, _clear_layout, as_float, as_int, closing_day_label, fill_combo, sentence_case, to_canonical
except ImportError:
    from ui_helpers import DATE_FORMAT, DATE_TIME_FORMAT, DISTRIBUTION_SPECS, FUNCTION_SPECS, POLICY_OPTIONS, POLICY_TYPE_PARAMS, _clear_layout, as_float, as_int, closing_day_label, fill_combo, sentence_case, to_canonical


class TimeFunctionWidget(QtWidgets.QWidget):

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.kind = QtWidgets.QComboBox()
        fill_combo(self.kind, list(FUNCTION_SPECS.keys()))
        lay.addWidget(self.kind)
        self._host = QtWidgets.QWidget()
        self._play = QtWidgets.QHBoxLayout(self._host)
        self._play.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        lay.addStretch(1)
        self._edits = {}
        self.kind.currentTextChanged.connect(self._rebuild)
        self.set_value(value or {"kind": "constant", "value": 0.0})

    def _rebuild(self, *_):
        _clear_layout(self._play)
        self._edits = {}
        for name, default in FUNCTION_SPECS[self.kind.currentData()]:
            self._play.addWidget(QtWidgets.QLabel(f"{sentence_case(name)}:"))
            e = QtWidgets.QLineEdit(str(default))
            e.setMaximumWidth(64)
            self._play.addWidget(e)
            self._edits[name] = e

    def set_value(self, value):
        value = value or {}
        kind = to_canonical(value.get("kind", "constant"), list(FUNCTION_SPECS.keys()))
        if kind not in FUNCTION_SPECS:
            kind = "constant"
        blocked = self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.kind.blockSignals(blocked)
        self._rebuild()
        for name, _ in FUNCTION_SPECS[kind]:
            if name in value and name in self._edits:
                self._edits[name].setText(str(value[name]))

    def get_value(self):
        kind = self.kind.currentData()
        out = {"kind": kind}
        for name, _ in FUNCTION_SPECS[kind]:
            out[name] = as_float(self._edits[name].text())
        return out


class SamplerWidget(QtWidgets.QWidget):

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Type:"))
        self.dist = QtWidgets.QComboBox()
        fill_combo(self.dist, list(DISTRIBUTION_SPECS.keys()))
        top.addWidget(self.dist)
        top.addStretch(1)
        lay.addLayout(top)
        self._host = QtWidgets.QWidget()
        self._form = QtWidgets.QFormLayout(self._host)
        self._form.setContentsMargins(12, 2, 0, 0)
        lay.addWidget(self._host)
        self._params = {}
        self.dist.currentTextChanged.connect(self._rebuild)
        self.set_value(value or {"dist_type": "Constant",
                                 "params": {"value": {"kind": "constant", "value": 0.0}}})

    def _rebuild(self, *_):
        while self._form.rowCount():
            self._form.removeRow(0)
        self._params = {}
        for pname, _ptype, pdefault in DISTRIBUTION_SPECS[self.dist.currentData()]:
            tf = TimeFunctionWidget(value={"kind": "constant", "value": pdefault})
            self._form.addRow(sentence_case(pname), tf)
            self._params[pname] = tf

    def set_value(self, value):
        value = value or {}
        dist_type = to_canonical(value.get("dist_type", "Constant"), list(DISTRIBUTION_SPECS.keys()))
        if dist_type not in DISTRIBUTION_SPECS:
            dist_type = "Constant"
        blocked = self.dist.blockSignals(True)
        self.dist.setCurrentIndex(max(0, self.dist.findData(dist_type)))
        self.dist.blockSignals(blocked)
        self._rebuild()
        params = value.get("params", {})
        for pname, tf in self._params.items():
            if pname in params:
                tf.set_value(params[pname])

    def get_value(self):
        return {"dist_type": self.dist.currentData(),
                "params": {n: w.get_value() for n, w in self._params.items()}}


class InfFloatWidget(QtWidgets.QWidget):

    def __init__(self, value="inf", parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.chk = QtWidgets.QCheckBox("Infinite")
        self.edit = QtWidgets.QLineEdit()
        self.edit.setMaximumWidth(90)
        lay.addWidget(self.chk)
        lay.addWidget(self.edit)
        lay.addStretch(1)
        self.chk.toggled.connect(self.edit.setDisabled)
        self.set_value(value)

    def set_value(self, value):
        infinite = (value in ("inf", "Infinity") or (isinstance(value, float) and value == float("inf")))
        self.chk.setChecked(infinite)
        self.edit.setDisabled(infinite)
        self.edit.setText("" if infinite else str(value))

    def get_value(self):
        return "inf" if self.chk.isChecked() else as_float(self.edit.text())


class HourMinuteWidget(QtWidgets.QWidget):

    def __init__(self, value="00:00", parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.h = QtWidgets.QLineEdit(); self.h.setMaximumWidth(48)
        self.m = QtWidgets.QLineEdit(); self.m.setMaximumWidth(48)
        lay.addWidget(self.h); lay.addWidget(QtWidgets.QLabel("h"))
        lay.addWidget(self.m); lay.addWidget(QtWidgets.QLabel("m"))
        lay.addStretch(1)
        self.set_value(value)

    def set_value(self, value):
        hh, _, mm = str(value).partition(":")
        self.h.setText(str(as_int(hh))); self.m.setText(str(as_int(mm)))

    def get_value(self):
        return f"{as_int(self.h.text()):02d}:{as_int(self.m.text()):02d}"


class DateTimeWidget(QtWidgets.QDateTimeEdit):

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat(DATE_TIME_FORMAT)
        self.set_value(value)

    def set_value(self, value):
        dt = QtCore.QDateTime.fromString(str(value or ""), DATE_TIME_FORMAT)
        if not dt.isValid():
            dt = QtCore.QDateTime(QtCore.QDate(2026, 1, 1), QtCore.QTime(0, 0))
        self.setDateTime(dt)

    def get_value(self):
        return self.dateTime().toString(DATE_TIME_FORMAT)


class DateWidget(QtWidgets.QDateEdit):

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat(DATE_FORMAT)
        self.set_value(value)

    def set_value(self, value):
        d = QtCore.QDate.fromString(str(value or ""), DATE_FORMAT)
        if not d.isValid():
            d = QtCore.QDate(2026, 1, 1)
        self.setDate(d)

    def get_value(self):
        return self.date().toString(DATE_FORMAT)


class ClosingDayPickerWidget(QtWidgets.QListWidget):

    def __init__(self, closing_days, chosen=None, parent=None):
        super().__init__(parent)
        chosen = set(chosen or [])
        known = set()
        for entry in (closing_days or []):
            it = QtWidgets.QListWidgetItem(closing_day_label(entry))
            it.setData(QtCore.Qt.UserRole, entry.get("date"))
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if entry.get("date") in chosen else QtCore.Qt.Unchecked)
            self.addItem(it)
            known.add(entry.get("date"))


        for date in sorted(chosen - known):
            it = QtWidgets.QListWidgetItem(f"{date} (not in registry)")
            it.setData(QtCore.Qt.UserRole, date)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked)
            self.addItem(it)

    def value(self):
        return [self.item(i).data(QtCore.Qt.UserRole) for i in range(self.count())
                if self.item(i).checkState() == QtCore.Qt.Checked]


class _IntervalRow(QtWidgets.QWidget):
    def __init__(self, start="08:00", end="17:00", on_remove=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.start = HourMinuteWidget(start)
        self.end = HourMinuteWidget(end)
        lay.addWidget(QtWidgets.QLabel("Start:")); lay.addWidget(self.start)
        lay.addWidget(QtWidgets.QLabel("End:")); lay.addWidget(self.end)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        if on_remove:
            rm.clicked.connect(lambda: on_remove(self))
        lay.addWidget(rm); lay.addStretch(1)

    def data(self):
        return {"start": self.start.get_value(), "end": self.end.get_value()}


class _DayRow(QtWidgets.QWidget):

    def __init__(self, label, working=False, intervals=None, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.chk = QtWidgets.QCheckBox(label)
        self.chk.setMinimumWidth(48)
        outer.addWidget(self.chk, 0, QtCore.Qt.AlignTop)
        self._box = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._box)
        self._vl.setContentsMargins(0, 0, 0, 0)
        self._rows = []
        add = QtWidgets.QPushButton("+ interval")
        add.clicked.connect(lambda: self._add())
        self._vl.addWidget(add)
        outer.addWidget(self._box, 1)
        self.chk.toggled.connect(self._box.setEnabled)
        for iv in (intervals or []):
            self._add(iv.get("start", "08:00"), iv.get("end", "17:00"))
        self.chk.setChecked(working)
        self._box.setEnabled(working)

    def _add(self, start="08:00", end="17:00"):
        row = _IntervalRow(start, end, on_remove=self._remove)
        self._rows.append(row)
        self._vl.insertWidget(self._vl.count() - 1, row)

    def _remove(self, row):
        if row in self._rows:
            self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()

    def data(self):
        return {"working": self.chk.isChecked(),
                "intervals": [r.data() for r in self._rows]}


class _CustomIntervalRow(QtWidgets.QWidget):

    def __init__(self, start="01-01-2026 08:00", end="01-01-2026 17:00", on_remove=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.start = DateTimeWidget(start)
        self.end = DateTimeWidget(end)
        lay.addWidget(QtWidgets.QLabel("From:")); lay.addWidget(self.start)
        lay.addWidget(QtWidgets.QLabel("To:")); lay.addWidget(self.end)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        if on_remove:
            rm.clicked.connect(lambda: on_remove(self))
        lay.addWidget(rm); lay.addStretch(1)

    def data(self):
        return {"start": self.start.get_value(), "end": self.end.get_value()}


class CustomIntervalListWidget(QtWidgets.QWidget):

    def __init__(self, intervals=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ interval"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        self._rows = []
        for iv in (intervals or []):
            self._add(iv.get("start"), iv.get("end"))

    def _add(self, start=None, end=None):
        row = _CustomIntervalRow(start or "01-01-2026 08:00", end or "01-01-2026 17:00",
                                 on_remove=self._remove)
        self._rows.append(row); self._vl.addWidget(row)

    def _remove(self, row):
        if row in self._rows:
            self._rows.remove(row); row.setParent(None); row.deleteLater()

    def value(self):
        return [r.data() for r in self._rows]


class ModelTreeWidget(QtWidgets.QTreeWidget):

    def __init__(self, model_registry, checked=None, leaves_only=False, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self._leaves_only = leaves_only
        self._items = {}
        checked = set(checked or [])
        children_of = {}
        for m in model_registry:
            children_of.setdefault(m.get("parent"), []).append(m["name"])
        has_children = {m["name"]: bool(children_of.get(m["name"])) for m in model_registry}

        def add(name, parent_item):
            item = QtWidgets.QTreeWidgetItem(parent_item, [name])
            selectable = not (leaves_only and has_children.get(name))
            if selectable:
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(0, QtCore.Qt.Checked if name in checked else QtCore.Qt.Unchecked)
            else:
                item.setFlags(QtCore.Qt.ItemIsEnabled)
            self._items[name] = item
            for child in children_of.get(name, []):
                add(child, item)

        for root in children_of.get(None, []):
            add(root, self)
        self.expandAll()
        self._guard = False
        self.itemChanged.connect(self._on_changed)

    def _on_changed(self, item, col):
        if self._guard:
            return
        self._guard = True
        state = item.checkState(0)
        if state == QtCore.Qt.Checked:
            self._set_descendants(item, QtCore.Qt.Checked)
        else:

            p = item.parent()
            while p is not None:
                if p.flags() & QtCore.Qt.ItemIsUserCheckable:
                    p.setCheckState(0, QtCore.Qt.Unchecked)
                p = p.parent()
        self._guard = False

    def _set_descendants(self, item, state):
        for i in range(item.childCount()):
            ch = item.child(i)
            if ch.flags() & QtCore.Qt.ItemIsUserCheckable:
                ch.setCheckState(0, state)
            self._set_descendants(ch, state)

    def checked_models(self):
        return [name for name, it in self._items.items()
                if (it.flags() & QtCore.Qt.ItemIsUserCheckable) and it.checkState(0) == QtCore.Qt.Checked]


class ShiftPickerWidget(QtWidgets.QListWidget):

    def __init__(self, shift_names, chosen=None, parent=None):
        super().__init__(parent)
        chosen = set(chosen or [])
        for nm in shift_names:
            it = QtWidgets.QListWidgetItem(nm)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if nm in chosen else QtCore.Qt.Unchecked)
            self.addItem(it)

    def chosen(self):
        return [self.item(i).text() for i in range(self.count())
                if self.item(i).checkState() == QtCore.Qt.Checked]


class ResourcePickerWidget(QtWidgets.QWidget):

    def __init__(self, resource_names, value_label="quantity", entries=None,
                 add_label="resource", integer=False, parent=None):
        super().__init__(parent)
        self._names = list(resource_names)
        self._label = value_label
        self._int = integer
        self._rows = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._host)
        self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton(f"+ {sentence_case(add_label)}")
        add.clicked.connect(lambda: self._add())
        lay.addWidget(add)
        for e in (entries or []):
            self._add(e.get("resource"), e.get("value", e.get("quantity", e.get("proportion", 1.0))))

    def _add(self, resource=None, value=1.0):
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if resource in self._names:
            combo.setCurrentText(resource)
        text = str(as_int(value)) if self._int else str(value)
        edit = QtWidgets.QLineEdit(text); edit.setMaximumWidth(70)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        h.addWidget(combo); h.addWidget(QtWidgets.QLabel(sentence_case(self._label) + ":")); h.addWidget(edit); h.addWidget(rm); h.addStretch(1)
        entry = (row, combo, edit)
        rm.clicked.connect(lambda: self._remove(entry))
        self._rows.append(entry)
        self._vl.addWidget(row)

    def _remove(self, entry):
        if entry in self._rows:
            self._rows.remove(entry)
            entry[0].setParent(None); entry[0].deleteLater()

    def entries(self):
        out = []
        for _, combo, edit in self._rows:
            if combo.currentText():
                value = as_int(edit.text()) if self._int else as_float(edit.text())
                out.append({"resource": combo.currentText(), "value": value})
        return out


class AlternativesWidget(QtWidgets.QWidget):

    def __init__(self, operator_names, value=None, parent=None):
        super().__init__(parent)
        self._names = list(operator_names)
        self._alts = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._host)
        self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Alternative (OR)")
        add.clicked.connect(lambda: self._add_alt())
        lay.addWidget(add)
        for alt in (value or []):
            self._add_alt(alt)

    def _add_alt(self, members=None):
        box = QtWidgets.QGroupBox(f"Alternative {len(self._alts) + 1} (all needed together)")
        bl = QtWidgets.QVBoxLayout(box)
        picker = ResourcePickerWidget(self._names, value_label="count", add_label="operator group", integer=True,
                                      entries=[{"resource": m.get("operator"), "value": m.get("count", 1)} for m in (members or [])])
        bl.addWidget(picker)
        rm = QtWidgets.QPushButton("Remove alternative")
        bl.addWidget(rm)
        entry = (box, picker)
        rm.clicked.connect(lambda: self._remove_alt(entry))
        self._alts.append(entry)
        self._vl.addWidget(box)

    def _remove_alt(self, entry):
        if entry in self._alts:
            self._alts.remove(entry)
            entry[0].setParent(None); entry[0].deleteLater()

    def get_value(self):
        out = []
        for _, picker in self._alts:
            members = [{"operator": e["resource"], "count": int(e["value"])} for e in picker.entries()]
            if members:
                out.append(members)
        return out


class PoliciesWidget(QtWidgets.QWidget):

    def __init__(self, value=None, parent=None, policy_options=None):
        super().__init__(parent)
        value = value or {}
        form = QtWidgets.QFormLayout(self)
        self._combos = {}
        self._params = {}
        for name, (options, default) in (policy_options or POLICY_OPTIONS).items():
            row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            saved = value.get(name, {})
            saved_type = to_canonical(saved.get("type", default), options)
            combo = QtWidgets.QComboBox(); fill_combo(combo, options, saved_type)
            h.addWidget(combo)
            lbl = QtWidgets.QLabel("")
            edit = QtWidgets.QLineEdit(); edit.setMaximumWidth(60)
            saved_spec = POLICY_TYPE_PARAMS.get(saved_type)
            if saved_spec is not None and saved_spec[0] in saved:
                edit.setText(str(saved[saved_spec[0]]))
            h.addWidget(lbl); h.addWidget(edit); h.addStretch(1)
            self._combos[name] = combo
            self._params[name] = (lbl, edit)
            def _upd(_=None, n=name):
                spec = POLICY_TYPE_PARAMS.get(self._combos[n].currentData())
                p_lbl, p_edit = self._params[n]
                p_lbl.setVisible(spec is not None)
                p_edit.setVisible(spec is not None)
                if spec is not None:
                    p_lbl.setText(sentence_case(spec[1]) + ":")
                    if not p_edit.text():
                        p_edit.setText(str(spec[2]))
            combo.currentTextChanged.connect(_upd)
            _upd()
            form.addRow(sentence_case(name), row)

    def get_value(self):
        out = {}
        for name, combo in self._combos.items():
            t = combo.currentData()
            entry = {"type": t}
            spec = POLICY_TYPE_PARAMS.get(t)
            if spec is not None:
                entry[spec[0]] = as_float(self._params[name][1].text(), spec[2])
            out[name] = entry
        return out


class FixedGoalsWidget(QtWidgets.QWidget):

    def __init__(self, leaf_model_names, entries=None, parent=None):
        super().__init__(parent)
        prior = {e.get("model"): e.get("goal", e.get("value", 0)) for e in (entries or [])}
        self._rows = []
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        if not leaf_model_names:
            form.addRow(QtWidgets.QLabel("(define at least one leaf model first)"))
        for name in leaf_model_names:
            edit = QtWidgets.QLineEdit(str(prior.get(name, 0)))
            edit.setMaximumWidth(90)
            form.addRow(name, edit)
            self._rows.append((name, edit))

    def value(self):
        return [{"model": name, "goal": as_int(edit.text())} for name, edit in self._rows]


class FixedModelProbsWidget(QtWidgets.QWidget):

    def __init__(self, leaf_model_names, entries=None, parent=None):
        super().__init__(parent)
        prior = {e.get("model"): e.get("probability") for e in (entries or [])}
        self._rows = []
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        if not leaf_model_names:
            form.addRow(QtWidgets.QLabel("(define at least one leaf model first)"))
        for name in leaf_model_names:
            is_free = name in prior and prior[name] is None
            probability = prior.get(name) or {"kind": "constant", "value": 0.0}
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            tf = TimeFunctionWidget(probability)
            tf.setDisabled(is_free)
            free_chk = QtWidgets.QCheckBox("Freeloader"); free_chk.setChecked(is_free)
            h.addWidget(tf); h.addWidget(free_chk); h.addStretch(1)
            entry = (name, tf, free_chk)
            free_chk.toggled.connect(lambda checked, e=entry: self._on_free_toggled(e, checked))
            self._rows.append(entry)
            form.addRow(name, row)

    def _on_free_toggled(self, entry, checked):
        if checked:
            for e in self._rows:
                if e is not entry and e[2].isChecked():
                    blocked = e[2].blockSignals(True); e[2].setChecked(False); e[2].blockSignals(blocked)
                    e[1].setDisabled(False)
        entry[1].setDisabled(checked)

    def value(self):
        return [{"model": name, "probability": None if free_chk.isChecked() else tf.get_value()}
                for name, tf, free_chk in self._rows]


class ModelConfigsWidget(QtWidgets.QWidget):

    def __init__(self, model_names, resource_names, entries=None, parent=None):
        super().__init__(parent)
        self._models = list(model_names); self._resources = list(resource_names)
        self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Model config"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        box = QtWidgets.QGroupBox(); bl = QtWidgets.QFormLayout(box)
        combo = QtWidgets.QComboBox(); combo.addItems(self._models)
        if entry.get("model") in self._models:
            combo.setCurrentText(entry["model"])
        bl.addRow("Model", combo)
        dur = SamplerWidget(entry.get("duration")); bl.addRow("Duration", dur)
        res = ResourcePickerWidget(self._resources, "quantity",
                                   [{"resource": r.get("resource"), "value": r.get("value", r.get("quantity", 1.0))}
                                    for r in entry.get("resources", [])])
        bl.addRow("Resources", res)
        mn = QtWidgets.QLineEdit(str(entry.get("min_carrier_capacity", 1))); mn.setMaximumWidth(60)
        mx = QtWidgets.QLineEdit(str(entry.get("max_carrier_capacity", 1))); mx.setMaximumWidth(60)
        bl.addRow("Min carrier capacity", mn); bl.addRow("Max carrier capacity", mx)
        rm = QtWidgets.QPushButton("Remove model"); bl.addRow(rm)
        rec = (box, combo, dur, res, mn, mx)
        rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec); self._vl.addWidget(box)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def value(self):
        out = []
        for _, combo, dur, res, mn, mx in self._rows:
            if not combo.currentText():
                continue
            out.append({
                "model": combo.currentText(),
                "duration": dur.get_value(),
                "resources": res.entries(),
                "min_carrier_capacity": as_int(mn.text(), 1),
                "max_carrier_capacity": as_int(mx.text(), 1),
            })
        return out


class _TransformedWidget(QtWidgets.QWidget):
    def __init__(self, resource_names, entries=None, parent=None):
        super().__init__(parent)
        self._names = list(resource_names); self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Transformed resource"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if entry.get("resource") in self._names:
            combo.setCurrentText(entry["resource"])
        prop = QtWidgets.QLineEdit(str(entry.get("proportion", 1.0))); prop.setMaximumWidth(60)
        salv = QtWidgets.QCheckBox("Salvageable"); salv.setChecked(bool(entry.get("salvageable", True)))
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        h.addWidget(combo); h.addWidget(QtWidgets.QLabel("Proportion:")); h.addWidget(prop); h.addWidget(salv); h.addWidget(rm); h.addStretch(1)
        rec = (row, combo, prop, salv); rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec); self._vl.addWidget(row)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def value(self):
        return [{"resource": c.currentText(), "proportion": as_float(p.text()), "salvageable": s.isChecked()}
                for _, c, p, s in self._rows if c.currentText()]


class _OutputsWidget(QtWidgets.QWidget):
    def __init__(self, resource_names, entries=None, parent=None):
        super().__init__(parent)
        self._names = list(resource_names); self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Output resource"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        box = QtWidgets.QGroupBox(); bl = QtWidgets.QFormLayout(box)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if entry.get("resource") in self._names:
            combo.setCurrentText(entry["resource"])
        bl.addRow("Resource", combo)
        dist = SamplerWidget(entry.get("distribution")); bl.addRow("Amount", dist)
        lb = QtWidgets.QLineEdit(str(entry.get("lowerbound", 0.0))); lb.setMaximumWidth(90)
        ub = QtWidgets.QLineEdit(str(entry.get("upperbound", 1.0))); ub.setMaximumWidth(90)
        bl.addRow("Lowerbound (≥ 0)", lb)
        bl.addRow("Upperbound (finite)", ub)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24); bl.addRow(rm)
        rec = (box, combo, dist, lb, ub); rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec); self._vl.addWidget(box)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def value(self):
        return [{"resource": c.currentText(), "distribution": d.get_value(),
                 "lowerbound": as_float(lb.text()), "upperbound": as_float(ub.text(), 1.0)}
                for _, c, d, lb, ub in self._rows if c.currentText()]
