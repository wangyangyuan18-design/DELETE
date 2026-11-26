# -*- coding: utf-8 -*-
"""
DELETE HUB BOX - QGIS plugin
All layer access and geometry operations run on the QGIS main thread.
Large scans are split into timer-driven batches to keep the UI responsive.
"""
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QProgressDialog
from qgis.core import QgsProject, QgsSpatialIndex, QgsGeometry, QgsFeature
from qgis.PyQt.QtCore import Qt


class DeleteHubBoxPlugin:
    HUB_LAYER = "HUB BOX"
    NAME_LAYERS = [
        "HUB BOX Boundary", "SUB BOX", "SUB BOX Boundary",
        "Distribution Cable"
    ]
    POINT_LAYERS = [
        "Sdus", "Mdus", "Church", "Commercial", "Hospitals",
        "Institution", "Offices"
    ]
    BATCH_SIZE = 350

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.jobs = []
        self.job_index = 0
        self.current = None
        self.deleted_names = []
        self.boundary_geometries = []
        self.polygon_index = None
        self.polygon_by_fid = {}
        self.report = []
        self.failed = False

    def initGui(self):
        self.action = QAction("DELETE HUB BOX", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&DELETE", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.timer.stop()
        if self.action:
            self.iface.removePluginMenu("&DELETE", self.action)
            self.iface.removeToolBarIcon(self.action)

    def _layers(self, name):
        return QgsProject.instance().mapLayersByName(name)

    def _layer(self, name):
        layers = self._layers(name)
        return layers[0] if layers else None

    def run(self):
        if self.timer.isActive():
            return
        layer = self._layer(self.HUB_LAYER)
        if layer is None:
            QMessageBox.warning(self.iface.mainWindow(), "DELETE HUB BOX",
                                '找不到图层 "HUB BOX"。')
            return
        selected = set(layer.selectedFeatureIds())
        if not selected:
            QMessageBox.warning(self.iface.mainWindow(), "DELETE HUB BOX",
                                '请先在 "HUB BOX" 图层中选择需要保留的要素。')
            return
        if not layer.fields().indexFromName("Name") >= 0:
            QMessageBox.critical(self.iface.mainWindow(), "DELETE HUB BOX",
                                 'HUB BOX 图层缺少 Name 字段。')
            return

        total = layer.featureCount()
        if len(selected) >= total:
            QMessageBox.information(self.iface.mainWindow(), "DELETE HUB BOX",
                                    "所有 HUB BOX 均已选中，无需删除。")
            return

        answer = QMessageBox.question(
            self.iface.mainWindow(), "确认级联删除",
            "将保留 HUB BOX 中当前选中的要素，并删除其余 HUB BOX。\n"
            "同时按 Name 关联删除 4 个图层，并按已删除的 SUB BOX Boundary "
            "多边形删除 7 个客户点图层中的点。\n\n"
            "此操作会修改数据。建议先备份工程及数据。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        self.deleted_names = []
        self.boundary_geometries = []
        self.polygon_by_fid = {}
        self.report = []
        self.failed = False
        self.jobs = []
        self.job_index = 0

        # HUB BOX is scanned first; unselected feature IDs are deleted.
        self.jobs.append(("hub", layer, None))
        for name in self.NAME_LAYERS:
            lyr = self._layer(name)
            if lyr is not None:
                if lyr.fields().indexFromName("Name") < 0:
                    QMessageBox.critical(
                        self.iface.mainWindow(), "DELETE HUB BOX",
                        '图层 "{}" 缺少 Name 字段，已取消操作。'.format(name))
                    return
                self.jobs.append(("name", lyr, name))
        for name in self.POINT_LAYERS:
            lyr = self._layer(name)
            if lyr is not None:
                self.jobs.append(("point", lyr, name))

        self.dialog = QProgressDialog("准备处理...", None, 0,
                                      len(self.jobs), self.iface.mainWindow())
        self.dialog.setWindowTitle("DELETE HUB BOX")
        self.dialog.setWindowModality(Qt.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setCancelButton(None)
        self.dialog.setValue(0)
        self._start_job()

    def _start_job(self):
        if self.job_index >= len(self.jobs):
            self._finish()
            return
        kind, layer, label = self.jobs[self.job_index]
        self.current = {
            "kind": kind, "layer": layer, "label": label,
            "iterator": iter(layer.getFeatures()), "delete_ids": [],
            "seen": 0, "matched": 0
        }
        self.dialog.setLabelText("正在处理：{}（{} / {}）".format(
            layer.name(), self.job_index + 1, len(self.jobs)))
        self.timer.start(1)

    def _tick(self):
        if not self.current:
            self.timer.stop()
            return
        c = self.current
        if c.get("phase") == "delete":
            self._delete_batch()
            return
        layer = c["layer"]
        kind = c["kind"]
        if kind == "hub":
            selected = set(layer.selectedFeatureIds())
            predicate = lambda f: f.id() not in selected
        elif kind == "name":
            names = tuple(n.casefold() for n in self.deleted_names)
            predicate = lambda f: bool(f["Name"]) and any(
                n in str(f["Name"]).casefold() for n in names)
        else:
            predicate = self._point_matches

        processed = 0
        try:
            while processed < self.BATCH_SIZE:
                try:
                    feat = next(c["iterator"])
                except StopIteration:
                    self._complete_job()
                    return
                processed += 1
                c["seen"] += 1
                if predicate(feat):
                    c["delete_ids"].append(feat.id())
                    c["matched"] += 1
                    if kind == "hub":
                        value = feat["Name"]
                        if value is not None and str(value).strip():
                            self.deleted_names.append(str(value).strip())
                    elif kind == "name" and layer.name() == "SUB BOX Boundary":
                        geom = QgsGeometry(feat.geometry())
                        if not geom.isNull() and not geom.isEmpty():
                            self.boundary_geometries.append((feat.id(), geom))
        except Exception as exc:
            self.timer.stop()
            self.failed = True
            QMessageBox.critical(self.iface.mainWindow(), "处理失败",
                                 "{}\n{}".format(layer.name(), exc))
            self._finish()
            return

    def _point_matches(self, feat):
        if not self.polygon_index or not self.polygon_by_fid:
            return False
        geom = feat.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            return False
        for fid in self.polygon_index.intersects(geom.boundingBox()):
            polygon = self.polygon_by_fid.get(fid)
            if polygon and polygon.intersects(geom):
                return True
        return False

    def _complete_job(self):
        self.timer.stop()
        c = self.current
        layer = c["layer"]
        ids = c["delete_ids"]
        c["delete_cursor"] = 0
        c["deleted_count"] = 0
        c["was_editable"] = layer.isEditable()

        if ids and not c["was_editable"] and not layer.startEditing():
            self.failed = True
            self.report.append("{}：无法进入编辑状态".format(layer.name()))
            self._after_job()
            return

        if ids:
            # Delete in small timer-driven batches; QgsVectorLayer.deleteFeature
            # respects an existing edit buffer and avoids blocking the UI.
            c["phase"] = "delete"
            self.timer.start(1)
        else:
            self._after_job()

    def _delete_batch(self):
        c = self.current
        layer = c["layer"]
        ids = c["delete_ids"]
        stop = min(c["delete_cursor"] + self.BATCH_SIZE, len(ids))
        while c["delete_cursor"] < stop:
            fid = ids[c["delete_cursor"]]
            c["delete_cursor"] += 1
            try:
                if layer.deleteFeature(fid):
                    c["deleted_count"] += 1
                else:
                    self.failed = True
                    self.report.append("{}：要素 FID {} 删除失败".format(layer.name(), fid))
            except Exception as exc:
                self.failed = True
                self.report.append("{}：FID {} 删除异常：{}".format(layer.name(), fid, exc))

        if c["delete_cursor"] < len(ids):
            return

        if not c["was_editable"]:
            if layer.commitChanges():
                pass
            else:
                errors = "; ".join(layer.commitErrors())
                layer.rollBack()
                self.failed = True
                self.report.append("{}：提交失败：{}".format(layer.name(), errors))
                c["deleted_count"] = 0
        self._after_job()

    def _after_job(self):
        c = self.current
        layer = c["layer"]
        self.report.append("{}：扫描 {}，删除/标记删除 {}".format(
            layer.name(), c["seen"], c["deleted_count"]))

        # Build polygon index after matching SUB BOX Boundary polygons are captured.
        if c["kind"] == "name" and layer.name() == "SUB BOX Boundary":
            self.polygon_index = QgsSpatialIndex()
            for fid, geom in self.boundary_geometries:
                f = QgsFeature()
                f.setId(fid)
                f.setGeometry(geom)
                self.polygon_index.addFeature(f)
                self.polygon_by_fid[fid] = geom

        self.dialog.setValue(self.job_index + 1)
        self.job_index += 1
        self.current = None
        QTimer.singleShot(0, self._start_job)

    def _finish(self):
        self.timer.stop()
        if self.dialog:
            self.dialog.close()
            self.dialog = None
        summary = (
            "被删除 HUB BOX 名称：{}\n".format(len(self.deleted_names)) +
            "提取待删除 SUB BOX Boundary 多边形：{}\n\n".format(
                len(self.boundary_geometries)) +
            "\n".join(self.report)
        )
        if self.failed:
            QMessageBox.warning(self.iface.mainWindow(), "DELETE HUB BOX - 完成（有错误）",
                                summary)
        else:
            QMessageBox.information(self.iface.mainWindow(), "DELETE HUB BOX - 完成",
                                    summary + "\n注意：原本处于编辑状态的图层，删除仍在编辑缓冲区中，"
                                    "需要你自行保存编辑。")
        self.current = None
        self.jobs = []
