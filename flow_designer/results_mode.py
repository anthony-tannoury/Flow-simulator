from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from Qt import QtCore, QtGui, QtWidgets


DUREE_COLS = {
    'temps_total', 'temps_ouverture', 'arrets_programmes', 'temps_requis',
    'pannes', 'mtbf', 'mttr', 'gel', 'mise_en_route', 'temps_fonctionnement',
    'cycle_moyen', 'cycle_p90', 'cycle_max',
    'attente_pieces', 'attente_place', 'attente_operateurs', 'attente_matiere',
    'attente_vague', 'temps_collecte', 'temps_chargement', 'temps_traitement',
    'heures_machine', 'heures_main_oeuvre', 'heures_en_poste', 'heures_hors_poste',
    'sejour_moyen', 'sejour_max', 'temps_moyen_entre_arrivees', 'temps_poste',
    'traversee_moyenne', 'traversee_mediane', 'traversee_p90', 'traversee_max',
    'temps_traversee', 'tc_ideal', 'duree_simulee', 'sim_end_minutes',
    'temps_rupture', 'heures_machine_totales', 'heures_main_oeuvre_totales',
}
PCT_COLS = {'taux_de_charge', 'disponibilite', 'performance', 'qualite',
            'trs', 'trg', 'tre', 'taux_rebut', 'atteinte', 'taux_occupation'}

LABEL_OVERRIDES = {
    'trs': 'TRS', 'trg': 'TRG', 'tre': 'TRE', 'mtbf': 'MTBF', 'mttr': 'MTTR',
    'tc_ideal': 'Tc idéal', 'cycle_p90': 'Cycle p90', 'traversee_p90': 'Traversée p90',
    'debit_pieces_j': 'Débit (pièces / jour)', 'debit_sorties_j': 'Débit sorties / jour',
    'flux_entrant_j': 'Flux entrant / jour', 'flux_sortant_j': 'Flux sortant / jour',
    'sim_end_minutes': 'Durée simulée', 'genere_le': 'Généré le', 'graine': 'Graine',
    'critere_arret': "Critère d'arrêt", 'critere_details': 'Détails du critère',
    'pieces_sorties': 'Pièces sorties', 'objectif_total': 'Objectif total',
    'objectif_atteint': 'Objectif atteint', 'source_file': 'Fichier source',
    'temps_calcul': 'Temps de calcul',
    'admin': 'Tâche administrative',
    'heures_machine': 'Heures machine',
    'heures_en_poste': 'Heures en poste', 'heures_hors_poste': 'Heures hors poste',
    'heures_main_oeuvre': "Heures main-d'\u0153uvre",
    'heures_machine_totales': 'Heures machine totales',
    'heures_main_oeuvre_totales': "Heures main-d'\u0153uvre totales",
    'capacite': 'Capacit\u00e9', 'consommation_totale': 'Consommation totale',
    'consommation_j': 'Consommation / jour', 'entrees_totales': 'Entr\u00e9es totales',
    'nb_ruptures': 'Nb ruptures', 'temps_rupture': 'Temps rupture',
}


def fmt_duree(minutes) -> str:
    if minutes in ('', None):
        return ''
    m = float(minutes)
    if m < 1:
        return f"{round(m * 60)}s"
    if m < 60:
        whole, secondes = int(m), round((m - int(m)) * 60)
        if secondes == 60:
            whole, secondes = whole + 1, 0
        if whole < 60:
            return f"{whole}m {secondes}s" if secondes else f"{whole}m"
    total = round(m)
    heures, mins = divmod(total, 60)
    if heures < 24:
        return f"{heures}h {mins}m"
    jours, heures = divmod(heures, 24)
    return f"{jours}j {heures}h {mins}m"


def fmt_pct(x) -> str:
    if x in ('', None):
        return ''
    return f"{float(x) * 100:.1f}".rstrip('0').rstrip('.') + '%'


def fmt_value(key: str, value) -> str:
    if value in ('', None):
        return ''
    if key in DUREE_COLS:
        return fmt_duree(value)
    if key in PCT_COLS:
        return fmt_pct(value)
    if isinstance(value, bool):
        return 'oui' if value else 'non'
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def pretty_label(key: str) -> str:
    if key in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[key]
    text = str(key).replace('_', ' ').strip()
    return (text[:1].upper() + text[1:]) if text else key


class ResultsData:

    def __init__(self, run_dir: str):
        self.run_dir = os.path.abspath(run_dir)
        path = os.path.join(self.run_dir, 'report.json')
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"No report.json in {run_dir}: this run predates interactive results; "
                f"re-run the simulation to browse it here.")
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('format') != 'flow-simulator-report':
            raise ValueError(f"{path} is not a flow-simulator report")
        self.data = data
        self.run = data.get('run', {})
        self.tasks = data.get('tasks', {})
        self.tasks_models = data.get('tasks_models', {})
        self.buffers = data.get('buffers', {})
        self.operators = data.get('operators', {})
        self.resources = data.get('resources', {})
        self.flux = data.get('flux', {})
        self.flux_modeles = data.get('flux_modeles', [])
        self.admin_summary = data.get('admin_summary', {})
        self.graphs = data.get('graphs', {})

    def node_ids(self) -> set:
        return set(self.tasks) | set(self.buffers)

    def snapshot_path(self) -> str:
        return os.path.join(self.run_dir, self.run.get('flow_snapshot', 'flow.json'))

    def graph_path(self, section: str, key: str | None = None) -> str | None:
        entry = self.graphs.get(section)
        rel = entry.get(key) if isinstance(entry, dict) else entry
        return os.path.join(self.run_dir, rel) if rel else None

    def outcome_text(self) -> str:
        produced = self.run.get('pieces_sorties')
        goal = self.run.get('objectif_total')
        if goal:
            if self.run.get('objectif_atteint'):
                return f"Objectif atteint : {produced} / {goal} pièces"
            return f"Objectif non atteint : {produced} / {goal} pièces"
        if produced is not None:
            return f"{produced} pièces sorties"
        return ''

    def run_label(self) -> str:
        stamp = os.path.basename(self.run_dir)
        criterion = self.run.get('critere_arret', '?')
        parts = [f"Run {stamp}", str(criterion)]
        outcome = self.outcome_text()
        if outcome:
            parts.append(outcome)
        return "   |   ".join(parts)


def _make_table(columns: list, rows: list) -> QtWidgets.QTableWidget:
    table = QtWidgets.QTableWidget(len(rows), len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QtWidgets.QTableWidgetItem(str(value))
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    return table


def kv_table(pairs: list) -> QtWidgets.QTableWidget:
    return _make_table(["", ""], [(label, value) for label, value in pairs])


_ADMIN_LABELS = {
    'nb_taches': 'Nombre de postes', 'temps_fonctionnement': 'Temps de fonctionnement',
    'cycle_total': 'Temps de cycle total', 'heures_machine': 'Heures machine',
    'heures_main_oeuvre': "Heures main-d'œuvre",
}
_ADMIN_DUREE = {'temps_fonctionnement', 'cycle_total', 'heures_machine', 'heures_main_oeuvre'}


def admin_table(summary: dict) -> QtWidgets.QTableWidget:
    def value(metric, group):
        v = summary.get(group, {}).get(metric, '')
        if not isinstance(v, (int, float)):
            return ''
        if metric in _ADMIN_DUREE:
            return fmt_duree(v)
        return str(int(v)) if float(v).is_integer() else f"{v:g}"

    def share(metric, group):
        v = summary.get(group, {}).get(metric, '')
        return fmt_pct(v) if isinstance(v, (int, float)) else ''

    def ratio(metric):
        v = summary.get('ratio_admin_sur_productif', {}).get(metric, '')
        return f"{v:g}" if isinstance(v, (int, float)) else ''

    cols = ["Indicateur", "Administratives", "Productives", "Total",
            "Part admin", "Part productif", "Ratio admin / prod"]
    rows = [[_ADMIN_LABELS.get(m, m), value(m, 'administratives'), value(m, 'productives'),
             value(m, 'total'), share(m, 'part_administratives'), share(m, 'part_productives'),
             ratio(m)] for m in summary.get('indicateurs', [])]
    return _make_table(cols, rows)


def dict_kv_pairs(data: dict, skip=()) -> list:
    return [(pretty_label(k), fmt_value(k, v)) for k, v in data.items() if k not in skip]


class PngView(QtWidgets.QWidget):

    def __init__(self, path: str | None, missing_text: str = "(graphique indisponible)", parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._path = path
        self._pixmap = QtGui.QPixmap(path) if path and os.path.isfile(path) else None
        if self._pixmap is None or self._pixmap.isNull():
            lay.addWidget(QtWidgets.QLabel(missing_text))
            self._pixmap = None
            return
        self._label = QtWidgets.QLabel()
        self._label.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._label)
        lay.addWidget(scroll, 1)
        open_btn = QtWidgets.QPushButton("Open image")
        open_btn.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(self._path)))
        h = QtWidgets.QHBoxLayout(); h.addStretch(1); h.addWidget(open_btn)
        lay.addLayout(h)
        self._rescale(940)

    def _rescale(self, width: int):
        if self._pixmap is not None:
            self._label.setPixmap(self._pixmap.scaledToWidth(
                min(width, self._pixmap.width()), QtCore.Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale(max(200, self.width() - 40))


class ResultsCardDialog(QtWidgets.QDialog):
    def __init__(self, parent, title: str, tabs: list):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 640)
        lay = QtWidgets.QVBoxLayout(self)
        widget = QtWidgets.QTabWidget()
        for name, tab in tabs:
            widget.addTab(tab, name)
        lay.addWidget(widget)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        bb.clicked.connect(lambda *_: self.accept())
        lay.addWidget(bb)


def _task_tabs(uid: str, results: ResultsData) -> list:
    raw = results.tasks[uid]

    order = ['type', 'temps_total', 'temps_ouverture', 'arrets_programmes', 'temps_requis',
             'temps_fonctionnement', 'pannes', 'nb_pannes', 'mtbf', 'mttr', 'gel',
             'mise_en_route', 'nb_mises_en_route',
             'taux_de_charge', 'disponibilite', 'performance', 'qualite', 'trs', 'trg', 'tre',
             'pieces_produites', 'pieces_bonnes', 'pieces_rebutees', 'nb_lancements',
             'taille_lot_moyenne', 'cycle_moyen', 'cycle_p90', 'cycle_max',
             'debit_pieces_j', 'flux_entrant_j', 'flux_sortant_j',
             'attente_pieces', 'attente_place', 'attente_operateurs', 'attente_matiere',
             'attente_vague', 'temps_collecte', 'temps_chargement', 'temps_traitement',
             'heures_machine', 'heures_main_oeuvre']
    pairs = [(pretty_label(k), fmt_value(k, raw[k])) for k in order if k in raw]
    pairs += [(pretty_label(k), fmt_value(k, v)) for k, v in raw.items()
              if k not in order and k != 'poste']
    tabs = [("KPIs", kv_table(pairs))]

    model_rows = results.tasks_models.get(uid)
    if model_rows:
        keys = [k for k in model_rows[0] if k != 'poste']
        tabs.append(("Par modèle", _make_table(
            [pretty_label(k) for k in keys],
            [[fmt_value(k, row.get(k)) for k in keys] for row in model_rows])))

    tabs.append(("Occupation", PngView(results.graph_path('tasks', uid))))
    return tabs


def _buffer_tabs(uid: str, results: ResultsData) -> list:
    raw = results.buffers[uid]
    tabs = [("Stock", kv_table(dict_kv_pairs(raw, skip=('buffer',))))]
    tabs.append(("Longueur", PngView(results.graph_path('buffers', uid))))
    return tabs


def _generator_tabs(results: ResultsData) -> list:
    tabs = [("Flux", kv_table(dict_kv_pairs(results.flux)))]
    if results.flux_modeles:
        keys = list(results.flux_modeles[0].keys())
        tabs.append(("Par modèle", _make_table(
            [pretty_label(k) for k in keys],
            [[fmt_value(k, row.get(k)) for k in keys] for row in results.flux_modeles])))
    tabs.append(("Production", PngView(results.graph_path('production'))))

    trajectories = results.graphs.get('models') or {}
    if trajectories:
        host = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(host)
        vl.setContentsMargins(0, 0, 0, 0)
        picker = QtWidgets.QComboBox()
        for model_name in sorted(trajectories):
            picker.addItem(model_name)
        vl.addWidget(picker)
        holder = QtWidgets.QVBoxLayout()
        vl.addLayout(holder, 1)

        def _show(model_name):
            while holder.count():
                item = holder.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            holder.addWidget(PngView(results.graph_path('models', model_name)))
        picker.currentTextChanged.connect(_show)
        _show(picker.currentText())
        tabs.append(("Trajectoires", host))
    return tabs


def card_dialog(parent, kind: str, uid: str, name: str, results: ResultsData):
    if kind in ("Task", "ResourceTask") and uid in results.tasks:
        return ResultsCardDialog(parent, f"Results: {name}", _task_tabs(uid, results))
    if kind == "Buffer" and uid in results.buffers:
        return ResultsCardDialog(parent, f"Results: {name}", _buffer_tabs(uid, results))
    if kind == "PieceGenerator":
        return ResultsCardDialog(parent, f"Results: {name}", _generator_tabs(results))
    return None


def card_tooltip(kind: str, uid: str, results: ResultsData) -> str | None:
    if kind in ("Task", "ResourceTask") and uid in results.tasks:
        raw = results.tasks[uid]
        bits = []
        if raw.get('trs') not in ('', None):
            bits.append(f"TRS {fmt_pct(raw['trs'])}")
        if raw.get('taux_de_charge') not in ('', None):
            bits.append(f"charge {fmt_pct(raw['taux_de_charge'])}")
        bits.append(f"{raw.get('pieces_produites', 0)} pièces")
        if raw.get('gel'):
            bits.append(f"gel {fmt_duree(raw['gel'])}")
        return " · ".join(bits)
    if kind == "Buffer" and uid in results.buffers:
        raw = results.buffers[uid]
        return (f"stock moyen {fmt_value('longueur_moyenne', raw.get('longueur_moyenne'))} · "
                f"max {raw.get('longueur_max')} · final {raw.get('longueur_finale')}")
    if kind == "PieceGenerator":
        return results.outcome_text() or None
    return None


class ResultsDock(QtWidgets.QDockWidget):
    def __init__(self, parent, results: ResultsData):
        super().__init__("Results", parent)
        self.setObjectName("results_dock")
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFloatable)
        tabs = QtWidgets.QTabWidget()

        run_pairs = [("Résultat", results.outcome_text())] if results.outcome_text() else []
        run_pairs += dict_kv_pairs(results.run, skip=('criterion', 'flow_snapshot',
                                                      'objectif_atteint', 'pieces_sorties',
                                                      'objectif_total'))
        tabs.addTab(kv_table(run_pairs), "Run")

        flux_host = QtWidgets.QWidget()
        fl = QtWidgets.QVBoxLayout(flux_host)
        fl.addWidget(kv_table(dict_kv_pairs(results.flux)))
        if results.flux_modeles:
            keys = list(results.flux_modeles[0].keys())
            fl.addWidget(_make_table(
                [pretty_label(k) for k in keys],
                [[fmt_value(k, row.get(k)) for k in keys] for row in results.flux_modeles]))
        tabs.addTab(flux_host, "Flux")

        if results.admin_summary:
            tabs.addTab(admin_table(results.admin_summary), "Admin")

        if results.operators:
            op_host = QtWidgets.QWidget()
            ol = QtWidgets.QVBoxLayout(op_host)
            op_ids = list(results.operators.keys())
            keys = list(next(iter(results.operators.values())).keys())
            table = _make_table(
                [pretty_label(k) for k in keys],
                [[fmt_value(k, results.operators[i].get(k)) for k in keys] for i in op_ids])
            ol.addWidget(table)
            show = QtWidgets.QPushButton("Show availability graph")
            def _show_selected():
                row = table.currentRow()
                if row < 0:
                    return
                uid = op_ids[row]
                name = results.operators[uid].get('groupe', uid)
                dlg = ResultsCardDialog(parent, f"Results: {name}",
                                        [("Disponibilité", PngView(results.graph_path('operators', uid)))])
                dlg.exec()
            show.clicked.connect(_show_selected)
            hb = QtWidgets.QHBoxLayout(); hb.addStretch(1); hb.addWidget(show)
            ol.addLayout(hb)
            tabs.addTab(op_host, "Opérateurs")

        if results.resources:
            res_host = QtWidgets.QWidget()
            rl = QtWidgets.QVBoxLayout(res_host)
            res_ids = list(results.resources.keys())
            res_keys = list(next(iter(results.resources.values())).keys())
            res_table = _make_table(
                [pretty_label(k) for k in res_keys],
                [[fmt_value(k, results.resources[i].get(k)) for k in res_keys] for i in res_ids])
            rl.addWidget(res_table)
            res_show = QtWidgets.QPushButton("Show stock graph")

            def _show_resource(_=None, table=res_table, ids=res_ids):
                row = table.currentRow()
                if row < 0:
                    return
                uid = ids[row]
                name = results.resources[uid].get('ressource', uid)
                dlg = ResultsCardDialog(parent, f"Results: {name}",
                                        [("Stock", PngView(results.graph_path('resources', uid)))])
                dlg.exec()
            res_show.clicked.connect(_show_resource)
            rhb = QtWidgets.QHBoxLayout(); rhb.addStretch(1); rhb.addWidget(res_show)
            rl.addLayout(rhb)
            tabs.addTab(res_host, "Ressources")

        line_tabs = QtWidgets.QTabWidget()
        line_tabs.addTab(PngView(results.graph_path('encours')), "Encours")
        line_tabs.addTab(PngView(results.graph_path('attente')), "Pièces en attente")
        tabs.addTab(line_tabs, "Ligne")

        open_row = QtWidgets.QWidget()
        orl = QtWidgets.QHBoxLayout(open_row)
        orl.setContentsMargins(6, 2, 6, 2)
        orl.addWidget(QtWidgets.QLabel(results.run_dir))
        orl.addStretch(1)
        open_btn = QtWidgets.QPushButton("Open run folder")
        open_btn.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(results.run_dir)))
        orl.addWidget(open_btn)

        host = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(tabs, 1)
        hl.addWidget(open_row)
        self.setWidget(host)


def _safe_ratio(num, den):
    try:
        return float(num) / float(den) if den not in ('', None, 0) and num not in ('', None) else None
    except (TypeError, ValueError):
        return None


def _get(raw, key):
    value = raw.get(key)
    return float(value) if value not in ('', None) else None


def _diff(raw, key_a, key_b):
    a, b = _get(raw, key_a), _get(raw, key_b)
    return a - b if a is not None and b is not None else None


def _passage_only(key):


    return lambda raw: _get(raw, key) if raw.get('type') == 'PASSAGE' else None


HEAT_METRICS = [
    ("Task: utilization (TF / TR)", 'tasks',
     lambda r: _safe_ratio(r.get('temps_fonctionnement'), r.get('temps_requis')), False),
    ("Task: TRS", 'tasks', lambda r: _get(r, 'trs'), True),
    ("Task: net flux (in - out, / day)", 'tasks',
     lambda r: _diff(r, 'flux_entrant_j', 'flux_sortant_j'), False),
    ("Task: throughput (pieces / day)", 'tasks', lambda r: _get(r, 'debit_pieces_j'), True),
    ("Task: scrap rate", 'tasks',
     lambda r: _safe_ratio(r.get('pieces_rebutees'), r.get('pieces_produites')), False),
    ("Task: freeze share (gel / TR)", 'tasks',
     lambda r: _safe_ratio(r.get('gel'), r.get('temps_requis')), False),
    ("Task: waiting for pieces", 'tasks', lambda r: _get(r, 'attente_pieces'), False),
    ("Task: waiting for operators", 'tasks', lambda r: _get(r, 'attente_operateurs'), False),
    ("Buffer: net flux (in - out, / day, passage)", 'buffers',
     lambda r: _diff(r, 'flux_entrant_j', 'flux_sortant_j') if r.get('type') == 'PASSAGE' else None, False),
    ("Buffer: average stock (passage)", 'buffers', _passage_only('longueur_moyenne'), False),
    ("Buffer: max stock (passage)", 'buffers', _passage_only('longueur_max'), False),
    ("Buffer: final stock (passage)", 'buffers', _passage_only('longueur_finale'), False),
    ("Buffer: average stay (passage)", 'buffers', _passage_only('sejour_moyen'), False),
    ("Buffer: scrap collected (rebuts)", 'buffers',
     lambda r: _get(r, 'entrees') if r.get('type') == 'SCRAP' else None, False),
]


DIMMED_COLOR = (74, 77, 82)


def heat_color(v01: float) -> tuple:
    v = min(1.0, max(0.0, v01))
    if v < 0.5:
        f = v / 0.5
        return (int(58 + (208 - 58) * f), int(140 + (170 - 140) * f), int(75 + (60 - 75) * f))
    f = (v - 0.5) / 0.5
    return (int(208 + (185 - 208) * f), int(170 + (60 - 170) * f), int(60 + (50 - 60) * f))


def heat_values(metric_index: int, results: ResultsData) -> dict:
    label, section, fn, higher_is_better = HEAT_METRICS[metric_index]
    rows = results.tasks if section == 'tasks' else results.buffers
    values = {uid: v for uid, raw in rows.items() if (v := fn(raw)) is not None}
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    span = (hi - lo) or 1.0
    out = {}
    for uid, v in values.items():
        v01 = (v - lo) / span
        if higher_is_better:
            v01 = 1.0 - v01
        out[uid] = heat_color(v01)
    return out
