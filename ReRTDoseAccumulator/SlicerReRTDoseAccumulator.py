import datetime
import html
import logging
import os

import ctk
import numpy as np
import qt
import slicer
import vtk
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleWidget,
    ScriptedLoadableModuleLogic,
)


class SlicerReRTDoseAccumulator(ScriptedLoadableModule):
    """3D Slicer scripted module for multi-course re-irradiation dose accumulation."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent.title = "ReRT Dose Accumulator"
        self.parent.categories = ["Reirradiation"]
        self.parent.dependencies = []
        self.parent.contributors = ["ReRT Dose Accumulator contributors"]
        self.parent.helpText = (
            "Research/physics-review tool for multi-course rigid or deformable CT/dose/structure registration, "
            "physical dose summation, EQD2 conversion, time-recovery discounting, and OAR "
            "metrics with structure-specific alpha/beta. Requires clinical physics QA before any clinical use."
        )
        self.parent.acknowledgementText = (
            "Built as a starter workflow for use with 3D Slicer and SlicerRT."
        )


class SlicerReRTDoseAccumulatorWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        super().setup()
        self.logic = SlicerReRTDoseAccumulatorLogic()
        self.treatmentRows = []
        self.lastMappedCourses = []
        self.lastStats = []
        self.lastDvhChartNode = None
        self.referenceButtonGroup = qt.QButtonGroup()
        self.referenceButtonGroup.setExclusive(True)

        self.layout.addWidget(self._heading("Multi-course Reirradiation Analysis"))
        intro = qt.QLabel(
            "Add any number of radiotherapy courses and explicitly mark the current treatment. "
            "All previous CT, RTDOSE, and RTSTRUCT data are mapped to that reference anatomy."
        )
        intro.setWordWrap(True)
        self.layout.addWidget(intro)

        treatmentsPanel = ctk.ctkCollapsibleButton()
        treatmentsPanel.text = "1. Treatment courses"
        treatmentsPanel.collapsed = False
        treatmentsPanelLayout = qt.QVBoxLayout(treatmentsPanel)
        self.treatmentsArea = qt.QScrollArea()
        self.treatmentsArea.widgetResizable = True
        self.treatmentsArea.setMinimumHeight(320)
        self.treatmentsContainer = qt.QWidget()
        self.treatmentsLayout = qt.QVBoxLayout(self.treatmentsContainer)
        self.treatmentsLayout.setContentsMargins(0, 0, 0, 0)
        self.treatmentsArea.setWidget(self.treatmentsContainer)
        treatmentsPanelLayout.addWidget(self.treatmentsArea)

        buttonsRow = qt.QWidget()
        buttonsLayout = qt.QHBoxLayout(buttonsRow)
        buttonsLayout.setContentsMargins(0, 0, 0, 0)
        self.addTreatmentButton = qt.QPushButton("Add treatment")
        self.addTreatmentButton.clicked.connect(self.onAddTreatment)
        buttonsLayout.addWidget(self.addTreatmentButton)
        treatmentsPanelLayout.addWidget(buttonsRow)
        self.currentTreatmentLabel = qt.QLabel("Current/reference treatment: not selected")
        self.currentTreatmentLabel.setWordWrap(True)
        self.currentTreatmentLabel.setStyleSheet(
            "padding: 8px; font-weight: bold; color: #ffffff; background-color: #2471a3;"
        )
        treatmentsPanelLayout.addWidget(self.currentTreatmentLabel)
        self.layout.addWidget(treatmentsPanel)

        registrationPanel = ctk.ctkCollapsibleButton()
        registrationPanel.text = "2. Registration and dose mapping"
        registrationLayout = qt.QVBoxLayout(registrationPanel)
        registrationInfo = qt.QLabel(
            "Previous courses are registered to the explicitly selected current/reference CT. "
            "Rigid is the conservative default; deformable mode performs rigid pre-alignment followed "
            "by B-spline registration. Review every registered CT and mapped contour before accumulation."
        )
        registrationInfo.setWordWrap(True)
        registrationLayout.addWidget(registrationInfo)
        self.registrationMode = qt.QComboBox()
        self.registrationMode.addItem("Rigid", "rigid")
        self.registrationMode.addItem("Rigid + affine + deformable B-spline", "deformable")
        self.registrationMode.setToolTip(
            "Deformable registration can improve anatomical correspondence but must be independently reviewed."
        )
        self.registrationMode.currentIndexChanged.connect(self._onRegistrationModeChanged)
        registrationLayout.addWidget(self._row("Registration method", self.registrationMode))
        self.mapAllButton = qt.QPushButton("Register all previous courses to current CT")
        self.mapAllButton.clicked.connect(self.onMapAll)
        registrationLayout.addWidget(self.mapAllButton)
        self.referenceCtSelector = self._volumeSelector("Reference CT used")
        registrationLayout.addWidget(self.referenceCtSelector["row"])
        self.layout.addWidget(registrationPanel)

        biologyPanel = ctk.ctkCollapsibleButton()
        biologyPanel.text = "3. Radiobiology and OAR selection"
        biologyLayout = qt.QVBoxLayout(biologyPanel)

        oarInfo = qt.QLabel(
            "Structures are loaded from the CURRENT / REFERENCE RTSTRUCT. "
            "Alpha/beta defaults to 3 Gy and can be edited independently for each structure. "
            "The displayed cumulative EQD2 map automatically uses the highest value in this table."
        )
        oarInfo.setWordWrap(True)
        biologyLayout.addWidget(oarInfo)
        self.oarTable = qt.QTableWidget()
        self.oarTable.setColumnCount(2)
        self.oarTable.setHorizontalHeaderLabels(["Structure from current RTSTRUCT", "Alpha/beta (Gy)"])
        self.oarTable.horizontalHeader().setStretchLastSection(True)
        self.oarTable.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
        self.oarTable.setSelectionMode(qt.QAbstractItemView.ExtendedSelection)
        self.oarTable.setMinimumHeight(190)
        self.oarTable.itemChanged.connect(self._onOarSettingsChanged)
        biologyLayout.addWidget(self.oarTable)
        oarButtonsRow = qt.QWidget()
        oarButtonsLayout = qt.QHBoxLayout(oarButtonsRow)
        oarButtonsLayout.setContentsMargins(0, 0, 0, 0)
        self.removeOarButton = qt.QPushButton("Remove selected structure(s)")
        self.removeOarButton.clicked.connect(self.onRemoveSelectedOars)
        self.populateOarsButton = qt.QPushButton("Reload structures from current RTSTRUCT")
        self.populateOarsButton.clicked.connect(self.onPopulateOarsFromRtStruct)
        oarButtonsLayout.addWidget(self.removeOarButton)
        oarButtonsLayout.addWidget(self.populateOarsButton)
        biologyLayout.addWidget(oarButtonsRow)
        self.layout.addWidget(biologyPanel)

        resultsPanel = ctk.ctkCollapsibleButton()
        resultsPanel.text = "4. Cumulative dose, DVH and report"
        resultsLayout = qt.QVBoxLayout(resultsPanel)
        self.outputName = qt.QLineEdit("Cumulative_EQD2_all_courses")
        self.physicalOutputName = qt.QLineEdit("Total_Physical_Dose_all_courses")
        self.overlayOpacity = self._doubleSpinBox("Dose overlay opacity", 0.45, 0.0, 1.0, 0.05)
        resultsLayout.addWidget(self._row("EQD2 output", self.outputName))
        resultsLayout.addWidget(self._row("Physical output", self.physicalOutputName))
        resultsLayout.addWidget(self.overlayOpacity["row"])

        runRow = qt.QWidget()
        runLayout = qt.QHBoxLayout(runRow)
        runLayout.setContentsMargins(0, 0, 0, 0)
        self.computeButton = qt.QPushButton("Calculate cumulative dose and OAR metrics")
        self.computeButton.clicked.connect(self.onCompute)
        self.showPhysicalButton = qt.QPushButton("Show total physical dose")
        self.showPhysicalButton.clicked.connect(self.onShowPhysical)
        self.showEqd2Button = qt.QPushButton("Show cumulative EQD2")
        self.showEqd2Button.clicked.connect(self.onShowEqd2)
        self.repairHierarchyButton = qt.QPushButton("Attach outputs to current DICOM study")
        self.repairHierarchyButton.clicked.connect(self.onRepairOutputHierarchy)
        runLayout.addWidget(self.computeButton)
        runLayout.addWidget(self.showPhysicalButton)
        runLayout.addWidget(self.showEqd2Button)
        runLayout.addWidget(self.repairHierarchyButton)
        resultsLayout.addWidget(runRow)

        self.outputPhysicalSelector = self._volumeSelector("Total physical dose")
        self.outputEqd2Selector = self._volumeSelector("Cumulative EQD2")
        self.analysisStructuresSelector = self._segmentationSelector("Current treatment RTSTRUCT used")
        self.analysisStructuresSelector["selector"].enabled = False
        resultsLayout.addWidget(self.outputPhysicalSelector["row"])
        resultsLayout.addWidget(self.outputEqd2Selector["row"])
        resultsLayout.addWidget(self.analysisStructuresSelector["row"])

        self.resultsTable = qt.QTableWidget()
        self.resultsTable.setColumnCount(7)
        self.resultsTable.setHorizontalHeaderLabels(
            [
                "OAR",
                "Alpha/beta (Gy)",
                "Recovery by prior course",
                "Volume (cc)",
                "Mean EQD2",
                "Dmax EQD2",
                "D2cc EQD2",
            ]
        )
        self.resultsTable.horizontalHeader().setStretchLastSection(True)
        self.resultsTable.setMinimumHeight(240)
        resultsLayout.addWidget(self.resultsTable)

        analysisRow = qt.QWidget()
        analysisLayout = qt.QHBoxLayout(analysisRow)
        analysisLayout.setContentsMargins(0, 0, 0, 0)
        self.dvhVolumeUnit = qt.QComboBox()
        self.dvhVolumeUnit.addItems(["Relative volume (%)", "Absolute volume (cc)"])
        self.dvhVisibleOnly = qt.QCheckBox("Visible structures only")
        self.dvhVisibleOnly.setChecked(True)
        self.showDvhButton = qt.QPushButton("Show cumulative EQD2 DVH")
        self.showDvhButton.clicked.connect(self.onShowDvh)
        self.exportPdfButton = qt.QPushButton("Export analysis report (PDF)")
        self.exportPdfButton.clicked.connect(self.onExportPdf)
        self.exportPdfButton.enabled = False
        analysisLayout.addWidget(self.dvhVolumeUnit)
        analysisLayout.addWidget(self.dvhVisibleOnly)
        analysisLayout.addWidget(self.showDvhButton)
        analysisLayout.addWidget(self.exportPdfButton)
        resultsLayout.addWidget(analysisRow)
        self.layout.addWidget(resultsPanel)

        self.statusLabel = qt.QLabel(
            "Load DICOM data with Slicer's DICOM browser first. SlicerRT can import RTDOSE and RTSTRUCT."
        )
        self.statusLabel.setWordWrap(True)
        self.layout.addWidget(self.statusLabel)
        self.layout.addStretch(1)

        self._addTreatment("Current treatment", monthsAgo=0, fractions=30, makeReference=True)

    def _heading(self, text):
        label = qt.QLabel(f"<b>{text}</b>")
        label.setStyleSheet("margin-top: 12px;")
        return label

    def _row(self, labelText, control):
        row = qt.QWidget()
        rowLayout = qt.QHBoxLayout(row)
        rowLayout.setContentsMargins(0, 0, 0, 0)
        label = qt.QLabel(labelText)
        label.setMinimumWidth(190)
        rowLayout.addWidget(label)
        rowLayout.addWidget(control)
        return row

    def _volumeSelector(self, labelText):
        selector = slicer.qMRMLNodeComboBox()
        selector.nodeTypes = ["vtkMRMLScalarVolumeNode"]
        selector.selectNodeUponCreation = False
        selector.addEnabled = False
        selector.removeEnabled = False
        selector.noneEnabled = True
        selector.showHidden = False
        selector.showChildNodeTypes = True
        selector.setMRMLScene(slicer.mrmlScene)
        return {"row": self._row(labelText, selector), "selector": selector}

    def _segmentationSelector(self, labelText):
        selector = slicer.qMRMLNodeComboBox()
        selector.nodeTypes = ["vtkMRMLSegmentationNode"]
        selector.selectNodeUponCreation = False
        selector.addEnabled = False
        selector.removeEnabled = False
        selector.noneEnabled = True
        selector.showHidden = False
        selector.showChildNodeTypes = False
        selector.setMRMLScene(slicer.mrmlScene)
        return {"row": self._row(labelText, selector), "selector": selector}

    def _transformSelector(self, labelText):
        selector = slicer.qMRMLNodeComboBox()
        selector.nodeTypes = ["vtkMRMLTransformNode"]
        selector.selectNodeUponCreation = False
        selector.addEnabled = False
        selector.removeEnabled = False
        selector.noneEnabled = True
        selector.showHidden = False
        selector.showChildNodeTypes = True
        selector.setMRMLScene(slicer.mrmlScene)
        return {"row": self._row(labelText, selector), "selector": selector}

    def _spinBox(self, labelText, value, minimum, maximum, step):
        control = qt.QSpinBox()
        control.minimum = minimum
        control.maximum = maximum
        control.singleStep = step
        control.value = value
        return {"row": self._row(labelText, control), "control": control}

    def _doubleSpinBox(self, labelText, value, minimum, maximum, step):
        control = qt.QDoubleSpinBox()
        control.minimum = minimum
        control.maximum = maximum
        control.singleStep = step
        control.value = value
        control.decimals = 3
        return {"row": self._row(labelText, control), "control": control}

    def _addTreatment(self, name=None, monthsAgo=0, fractions=30, makeReference=True):
        index = len(self.treatmentRows) + 1
        group = qt.QGroupBox(name or f"Treatment {index}")
        groupLayout = qt.QVBoxLayout(group)

        referenceButton = qt.QRadioButton("CURRENT / REFERENCE TREATMENT")
        referenceButton.setToolTip(
            "This course defines the fixed CT and dose grid used for registration and accumulation."
        )
        referenceButton.setStyleSheet("font-weight: bold; color: #2e86c1; padding: 6px;")
        self.referenceButtonGroup.addButton(referenceButton)
        nameEdit = qt.QLineEdit(name or f"Treatment {index}")
        siteEdit = qt.QLineEdit("")
        siteEdit.placeholderText = "Free text, for example brain, C-spine, pelvis, breast, prostate"
        ctSelector = self._volumeSelector("CT")
        doseSelector = self._volumeSelector("RT dose")
        structureSelector = self._segmentationSelector("RTSTRUCT / segmentation")
        fractionsBox = self._spinBox("Fractions", fractions, 1, 200, 1)
        monthsBox = self._doubleSpinBox("Months before current treatment", monthsAgo, 0.0, 600.0, 1.0)
        recoveryBox = self._doubleSpinBox(
            "Auto conservative recovery for display map",
            0.0,
            0.0,
            1.0,
            0.05,
        )
        recoveryBox["control"].setReadOnly(True)
        recoveryBox["control"].setToolTip(
            "Automatically derived from months since treatment and the selected current-treatment OARs."
        )

        mappedDoseSelector = self._volumeSelector("Mapped dose on current/reference CT")
        mappedStructureSelector = self._segmentationSelector("Mapped structures")
        registeredCtSelector = self._volumeSelector("Registered CT for QA")
        registrationTransformSelector = self._transformSelector("Registration transform for QA")

        groupLayout.addWidget(referenceButton)
        groupLayout.addWidget(self._row("Treatment name", nameEdit))
        groupLayout.addWidget(self._row("Treatment site", siteEdit))
        groupLayout.addWidget(ctSelector["row"])
        groupLayout.addWidget(doseSelector["row"])
        groupLayout.addWidget(structureSelector["row"])
        groupLayout.addWidget(fractionsBox["row"])
        groupLayout.addWidget(monthsBox["row"])
        groupLayout.addWidget(recoveryBox["row"])
        groupLayout.addWidget(registeredCtSelector["row"])
        groupLayout.addWidget(registrationTransformSelector["row"])
        groupLayout.addWidget(mappedDoseSelector["row"])
        groupLayout.addWidget(mappedStructureSelector["row"])

        removeButton = qt.QPushButton("Remove this treatment")
        removeButton.clicked.connect(lambda checked=False, g=group: self.onRemoveTreatment(g))
        groupLayout.addWidget(removeButton)

        row = {
            "group": group,
            "reference": referenceButton,
            "name": nameEdit,
            "site": siteEdit,
            "ct": ctSelector["selector"],
            "dose": doseSelector["selector"],
            "structures": structureSelector["selector"],
            "fractions": fractionsBox["control"],
            "months": monthsBox["control"],
            "recovery": recoveryBox["control"],
            "mappedDose": mappedDoseSelector["selector"],
            "mappedStructures": mappedStructureSelector["selector"],
            "registeredCt": registeredCtSelector["selector"],
            "registrationTransform": registrationTransformSelector["selector"],
        }
        self.treatmentRows.append(row)
        self.treatmentsLayout.addWidget(group)
        nameEdit.textChanged.connect(lambda text: self._refreshTreatmentTitles())
        referenceButton.toggled.connect(
            lambda checked, r=row: self._onReferenceChanged(r, checked)
        )
        structureSelector["selector"].connect(
            "currentNodeChanged(vtkMRMLNode*)",
            lambda node, r=row: self._onTreatmentStructuresChanged(r, node),
        )
        monthsBox["control"].valueChanged.connect(
            lambda value, r=row: self._updateCourseRecovery(r)
        )
        if makeReference:
            referenceButton.setChecked(True)
        self._updateCourseRecovery(row)
        self._refreshTreatmentTitles()

    def onAddTreatment(self):
        hasReference = any(row["reference"].isChecked() for row in self.treatmentRows)
        self._addTreatment(makeReference=not hasReference)

    def onRemoveTreatment(self, group):
        if len(self.treatmentRows) <= 1:
            slicer.util.errorDisplay("Keep at least one treatment.")
            return
        removedRow = next(row for row in self.treatmentRows if row["group"] == group)
        wasReference = removedRow["reference"].isChecked()
        self.referenceButtonGroup.removeButton(removedRow["reference"])
        self.treatmentRows = [row for row in self.treatmentRows if row["group"] != group]
        group.setParent(None)
        if wasReference and self.treatmentRows:
            self.treatmentRows[-1]["reference"].setChecked(True)
        self._refreshTreatmentTitles()

    def _onReferenceChanged(self, row, checked):
        self._refreshTreatmentTitles()
        self._updateAllCourseRecoveries()
        if checked:
            self._syncCurrentTreatmentStructures()

    def _onTreatmentStructuresChanged(self, row, node):
        if row["reference"].isChecked():
            self._syncCurrentTreatmentStructures()

    def _currentTreatmentRow(self):
        return next(
            (row for row in self.treatmentRows if row["reference"].isChecked()), None
        )

    def _registrationModeKey(self):
        mode = self.registrationMode.itemData(self.registrationMode.currentIndex)
        return str(mode) if mode else "rigid"

    def _onRegistrationModeChanged(self, index=None):
        for row in self.treatmentRows:
            row["mappedDose"].setCurrentNode(None)
            row["mappedStructures"].setCurrentNode(None)
            row["registeredCt"].setCurrentNode(None)
            row["registrationTransform"].setCurrentNode(None)
        self.lastMappedCourses = []
        if hasattr(self, "statusLabel"):
            self.statusLabel.text = (
                "Registration method changed. Run registration again before dose accumulation."
            )

    def _currentTreatmentStructures(self):
        row = self._currentTreatmentRow()
        return row["structures"].currentNode() if row is not None else None

    def _syncCurrentTreatmentStructures(self):
        if not hasattr(self, "analysisStructuresSelector"):
            return
        segmentationNode = self._currentTreatmentStructures()
        self.analysisStructuresSelector["selector"].setCurrentNode(segmentationNode)
        if hasattr(self, "oarTable"):
            self.populateOarsFromSegmentation(segmentationNode, replace=True)

    def _refreshTreatmentTitles(self):
        for index, row in enumerate(self.treatmentRows):
            suffix = " - CURRENT / REFERENCE" if row["reference"].isChecked() else " - previous"
            row["group"].title = f"{index + 1}. {row['name'].text}{suffix}"
            if row["reference"].isChecked():
                row["group"].setStyleSheet(
                    "QGroupBox { font-weight: bold; border: 2px solid #2e86c1; margin-top: 8px; padding-top: 8px; }"
                )
            else:
                row["group"].setStyleSheet("QGroupBox { font-weight: bold; }")
        referenceRow = next(
            (row for row in self.treatmentRows if row["reference"].isChecked()), None
        )
        if hasattr(self, "currentTreatmentLabel"):
            self.currentTreatmentLabel.text = (
                f"Current/reference treatment: {referenceRow['name'].text}"
                if referenceRow is not None
                else "Current/reference treatment: not selected"
            )

    def _courseInputs(self):
        courses = []
        for row in self.treatmentRows:
            courses.append(
                {
                    "name": row["name"].text.strip() or row["group"].title,
                    "isReference": row["reference"].isChecked(),
                    "site": row["site"].text.strip(),
                    "ct": row["ct"].currentNode(),
                    "dose": row["dose"].currentNode(),
                    "structures": row["structures"].currentNode(),
                    "fractions": row["fractions"].value,
                    "months": row["months"].value,
                    "recovery": row["recovery"].value,
                    "mappedDoseSelector": row["mappedDose"],
                    "mappedStructuresSelector": row["mappedStructures"],
                    "registeredCtSelector": row["registeredCt"],
                    "registrationTransformSelector": row["registrationTransform"],
                    "registrationMode": self._registrationModeKey(),
                }
            )
        return courses

    def _oarSettings(self):
        settings = []
        for rowIndex in range(self.oarTable.rowCount):
            nameItem = self.oarTable.item(rowIndex, 0)
            alphaBetaItem = self.oarTable.item(rowIndex, 1)
            if nameItem is None or alphaBetaItem is None:
                continue
            name = nameItem.text().strip()
            try:
                alphaBeta = float(alphaBetaItem.text())
            except ValueError as exc:
                raise ValueError(f"Alpha/beta for '{name}' must be a number.") from exc
            if alphaBeta <= 0.0:
                raise ValueError(f"Alpha/beta for '{name}' must be greater than zero.")
            if name:
                settings.append({"segmentName": name, "alphaBeta": alphaBeta})
        if not settings:
            raise ValueError("Keep at least one RTSTRUCT structure for EQD2 analysis.")
        return settings

    def _onOarSettingsChanged(self, item=None):
        self.lastStats = []
        self._updateAllCourseRecoveries()
        if hasattr(self, "resultsTable"):
            self.resultsTable.setRowCount(0)
        if hasattr(self, "exportPdfButton"):
            self.exportPdfButton.enabled = False

    def _selectedOarNames(self):
        if not hasattr(self, "oarTable"):
            return []
        names = []
        for rowIndex in range(self.oarTable.rowCount):
            item = self.oarTable.item(rowIndex, 0)
            if item is not None and item.text().strip():
                names.append(item.text().strip())
        return names

    def _updateCourseRecovery(self, row):
        if row is None:
            return
        if row["reference"].isChecked():
            recovery = 0.0
        else:
            recovery = self.logic.conservativeMapRecoveryFactor(
                self._selectedOarNames(), row["months"].value
            )
        row["recovery"].value = recovery

    def _updateAllCourseRecoveries(self):
        for row in self.treatmentRows:
            self._updateCourseRecovery(row)

    def onRemoveSelectedOars(self):
        selectedRows = sorted(
            {index.row() for index in self.oarTable.selectedIndexes()}, reverse=True
        )
        if not selectedRows:
            slicer.util.errorDisplay("Select one or more structure rows to remove.")
            return
        for rowIndex in selectedRows:
            self.oarTable.removeRow(rowIndex)
        self._onOarSettingsChanged()
        self.statusLabel.text = "Structure selection changed. Recalculate OAR metrics before export."

    def onPopulateOarsFromRtStruct(self):
        try:
            segmentationNode = self._currentTreatmentStructures()
            if segmentationNode is None:
                raise ValueError("Select the RTSTRUCT in the CURRENT / REFERENCE treatment.")
            self.populateOarsFromSegmentation(segmentationNode, replace=True)
            self.statusLabel.text = f"Structure list reloaded from {segmentationNode.GetName()}."
        except Exception as exc:
            logging.exception(exc)
            slicer.util.errorDisplay(str(exc))

    def populateOarsFromSegmentation(self, segmentationNode, replace=False):
        preservedAlphaBeta = {}
        for rowIndex in range(self.oarTable.rowCount):
            nameItem = self.oarTable.item(rowIndex, 0)
            alphaBetaItem = self.oarTable.item(rowIndex, 1)
            if nameItem is not None and alphaBetaItem is not None:
                preservedAlphaBeta[nameItem.text().strip().lower()] = alphaBetaItem.text()
        if replace:
            self.oarTable.setRowCount(0)
        if segmentationNode is None:
            return
        existing = set()
        for rowIndex in range(self.oarTable.rowCount):
            item = self.oarTable.item(rowIndex, 0)
            if item is not None:
                existing.add(item.text().strip().lower())
        segmentation = segmentationNode.GetSegmentation()
        for index in range(segmentation.GetNumberOfSegments()):
            segmentName = segmentation.GetNthSegment(index).GetName()
            if segmentName.strip().lower() not in existing:
                rowIndex = self.oarTable.rowCount
                self.oarTable.insertRow(rowIndex)
                nameItem = qt.QTableWidgetItem(segmentName)
                nameItem.setFlags(nameItem.flags() & ~qt.Qt.ItemIsEditable)
                self.oarTable.setItem(rowIndex, 0, nameItem)
                alphaBeta = preservedAlphaBeta.get(segmentName.strip().lower(), "3.0")
                self.oarTable.setItem(rowIndex, 1, qt.QTableWidgetItem(alphaBeta))

    def onMapAll(self):
        try:
            courses = self._courseInputs()
            registrationMode = self._registrationModeKey()
            mappedCourses, referenceCt = self.logic.mapCoursesToLatestCt(
                courses, registrationMode
            )
            for course, mapped in zip(courses, mappedCourses):
                course["mappedDoseSelector"].setCurrentNode(mapped.get("dose"))
                course["mappedStructuresSelector"].setCurrentNode(mapped.get("structures"))
                course["registeredCtSelector"].setCurrentNode(mapped.get("registeredCt"))
                course["registrationTransformSelector"].setCurrentNode(mapped.get("transform"))
            self.referenceCtSelector["selector"].setCurrentNode(referenceCt)
            self.lastMappedCourses = mappedCourses
            self.statusLabel.text = (
                f"{self.logic.registrationModeLabel(registrationMode)} mapping completed. "
                "Inspect every registered CT, transform, and mapped structure before dose accumulation."
            )
        except Exception as exc:
            logging.exception(exc)
            slicer.util.errorDisplay(str(exc))

    def onCompute(self):
        try:
            self._updateAllCourseRecoveries()
            courses = self._courseInputs()
            mappedCourses, referenceCt = self.logic.ensureCoursesAreOnLatestCt(
                courses, self._registrationModeKey()
            )
            oarSettings = self._oarSettings()
            displayAlphaBeta = max(setting["alphaBeta"] for setting in oarSettings)
            physical, eqd2 = self.logic.computeMultiCourseDose(
                mappedCourses=mappedCourses,
                alphaBeta=displayAlphaBeta,
                physicalOutputName=self.physicalOutputName.text,
                eqd2OutputName=self.outputName.text,
            )
            self.referenceCtSelector["selector"].setCurrentNode(referenceCt)
            self.outputPhysicalSelector["selector"].setCurrentNode(physical)
            self.outputEqd2Selector["selector"].setCurrentNode(eqd2)
            self.logic.attachDerivedDoseToReferenceStudy(physical, referenceCt, "Gy")
            self.logic.attachDerivedDoseToReferenceStudy(eqd2, referenceCt, "Gy_EQD2")
            self.logic.showDoseOnCt(referenceCt, eqd2, self.overlayOpacity["control"].value, True)
            self._populateResults(mappedCourses, oarSettings)
            self.lastMappedCourses = mappedCourses
            self.exportPdfButton.enabled = True
            self.statusLabel.text = (
                f"Created {physical.GetName()} and {eqd2.GetName()} using alpha/beta={displayAlphaBeta:g} Gy "
                "for the display map. OAR metrics use structure-specific values."
            )
        except Exception as exc:
            logging.exception(exc)
            slicer.util.errorDisplay(str(exc))

    def _populateResults(self, mappedCourses, oarSettings):
        referenceStructures = self._currentTreatmentStructures()
        self.analysisStructuresSelector["selector"].setCurrentNode(referenceStructures)
        stats = self.logic.computeOarSpecificDoseStats(
            referenceStructures, mappedCourses, oarSettings
        )
        self.lastStats = stats
        self.resultsTable.setRowCount(len(stats))
        for rowIndex, item in enumerate(stats):
            values = [
                item["segmentName"],
                f"{item['alphaBeta']:.2f}",
                item["recoverySummary"],
                f"{item['volumeCc']:.2f}",
                f"{item['mean']:.2f}",
                f"{item['max']:.2f}",
                f"{item['d2cc']:.2f}",
            ]
            for colIndex, value in enumerate(values):
                self.resultsTable.setItem(rowIndex, colIndex, qt.QTableWidgetItem(value))

    def onShowPhysical(self):
        self.logic.showDoseOnCt(
            self.referenceCtSelector["selector"].currentNode(),
            self.outputPhysicalSelector["selector"].currentNode(),
            self.overlayOpacity["control"].value,
            True,
        )

    def onShowEqd2(self):
        self.logic.showDoseOnCt(
            self.referenceCtSelector["selector"].currentNode(),
            self.outputEqd2Selector["selector"].currentNode(),
            self.overlayOpacity["control"].value,
            True,
        )

    def onRepairOutputHierarchy(self):
        try:
            referenceCt = self.referenceCtSelector["selector"].currentNode()
            physical = self.outputPhysicalSelector["selector"].currentNode()
            eqd2 = self.outputEqd2Selector["selector"].currentNode()
            if referenceCt is None:
                raise ValueError("Select the current/reference CT first.")
            repaired = []
            if physical is not None:
                self.logic.attachDerivedDoseToReferenceStudy(physical, referenceCt, "Gy")
                repaired.append(physical.GetName())
            if eqd2 is not None:
                self.logic.attachDerivedDoseToReferenceStudy(eqd2, referenceCt, "Gy_EQD2")
                repaired.append(eqd2.GetName())
            if not repaired:
                raise ValueError("Select at least one physical or cumulative EQD2 output volume.")
            self.statusLabel.text = "Attached to current DICOM study: " + ", ".join(repaired)
            qt.QMessageBox.information(
                None,
                "DICOM hierarchy updated",
                "The selected outputs are now under the current treatment study and tagged as dose volumes.",
            )
        except Exception as exc:
            logging.exception(exc)
            slicer.util.errorDisplay(str(exc))

    def onShowDvh(self):
        try:
            segmentationNode = self._currentTreatmentStructures()
            if segmentationNode is None:
                raise ValueError("Select the RTSTRUCT in the CURRENT / REFERENCE treatment.")
            self._updateAllCourseRecoveries()
            mappedCourses, referenceCt = self.logic.ensureCoursesAreOnLatestCt(
                self._courseInputs(), self._registrationModeKey()
            )
            absoluteVolume = self.dvhVolumeUnit.currentIndex == 1
            self.lastDvhChartNode = self.logic.createOarSpecificDvhPlot(
                segmentationNode,
                mappedCourses,
                self._oarSettings(),
                absoluteVolume,
                self.dvhVisibleOnly.isChecked(),
            )
            self.referenceCtSelector["selector"].setCurrentNode(referenceCt)
            self.statusLabel.text = "OAR-specific cumulative EQD2 DVH generated."
        except Exception as exc:
            logging.exception(exc)
            slicer.util.errorDisplay(str(exc))

    def onExportPdf(self):
        try:
            if not self.lastStats:
                raise ValueError("Calculate cumulative dose and OAR metrics before exporting the report.")
            defaultName = f"ReRT_Report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            savePath = qt.QFileDialog.getSaveFileName(
                None, "Save reirradiation analysis report", defaultName, "PDF files (*.pdf)"
            )
            if isinstance(savePath, (tuple, list)):
                savePath = savePath[0]
            if not savePath:
                return
            savePath = str(savePath)
            if not savePath.lower().endswith(".pdf"):
                savePath += ".pdf"
            self._writePdfReport(savePath)
            self.statusLabel.text = f"PDF report saved: {savePath}"
            qt.QMessageBox.information(None, "Report saved", f"Report saved to:\n{savePath}")
        except Exception as exc:
            logging.exception(exc)
            slicer.util.errorDisplay(str(exc))

    def _writePdfReport(self, savePath):
        courses = self._courseInputs()
        referenceCt = self.referenceCtSelector["selector"].currentNode()
        eqd2Node = self.outputEqd2Selector["selector"].currentNode()
        currentStructures = self._currentTreatmentStructures()
        if referenceCt is None or eqd2Node is None:
            raise ValueError("Reference CT and cumulative EQD2 dose are required for the report.")
        if currentStructures is None:
            raise ValueError("The CURRENT / REFERENCE treatment must have an RTSTRUCT selected.")
        patientName, patientId = self._patientInfo(referenceCt)
        courseRows = []
        for index, course in enumerate(courses, 1):
            role = "CURRENT / REFERENCE" if course["isReference"] else "Previous"
            registrationLabel = (
                "Reference grid"
                if course["isReference"]
                else self.logic.registrationModeLabel(self._registrationModeKey())
            )
            ctName = course["ct"].GetName() if course["ct"] is not None else "Not selected"
            doseName = course["dose"].GetName() if course["dose"] is not None else "Not selected"
            courseRows.append(
                "<tr>"
                f"<td>{index}</td><td>{html.escape(course['name'])}</td><td>{role}</td>"
                f"<td>{html.escape(course['site'] or 'Not specified')}</td>"
                f"<td>{html.escape(ctName)}</td><td>{html.escape(doseName)}</td>"
                f"<td>{html.escape(registrationLabel)}</td>"
                f"<td>{course['fractions']}</td><td>{course['months']:.1f}</td>"
                f"<td>{course['recovery']:.2f}</td></tr>"
            )

        resultRows = []
        for row in range(self.resultsTable.rowCount):
            cells = []
            for column in range(self.resultsTable.columnCount):
                item = self.resultsTable.item(row, column)
                cells.append(html.escape(item.text() if item is not None else ""))
            resultRows.append("<tr>" + "".join(f"<td>{value}</td>" for value in cells) + "</tr>")

        generatedAt = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        displayAlphaBeta = float(
            eqd2Node.GetAttribute("ReRT.AlphaBeta")
            or max(setting["alphaBeta"] for setting in self._oarSettings())
        )
        overlayImagePaths, maxDose, thresholdDose = self._captureDoseOverlaysForReport(
            referenceCt, eqd2Node
        )
        overlayImageUrls = {
            projection: qt.QUrl.fromLocalFile(path).toString()
            for projection, path in overlayImagePaths.items()
        }
        reportHtml = f"""
        <html><head><style>
        body {{ font-family: Arial, sans-serif; color: #263238; font-size: 9pt; }}
        h1 {{ color: #1f618d; text-align: center; font-size: 20pt; }}
        h2 {{ color: #1f618d; margin-top: 18px; font-size: 13pt; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
        th, td {{ border: 1px solid #cfd8dc; padding: 5px; vertical-align: top; }}
        th {{ background-color: #eaf2f8; font-weight: bold; }}
        .meta {{ color: #607d8b; text-align: right; }}
        .projection-table td {{ border: 0; padding: 3px; text-align: center; }}
        .dose-image {{ width: 165px; }}
        .projection-label {{ color: #455a64; font-size: 8pt; font-weight: bold; }}
        .dose-section {{ page-break-before: always; }}
        .caption {{ color: #607d8b; font-size: 8pt; text-align: center; }}
        .warning {{ margin-top: 18px; padding: 8px; background-color: #fff3cd; color: #5d4e00; }}
        </style></head><body>
        <h1>Multi-course Reirradiation Analysis</h1>
        <div class="meta">Generated {generatedAt}</div>
        <h2>Patient and model</h2>
        <table>
        <tr><th>Patient name</th><td>{html.escape(patientName)}</td><th>Patient ID</th><td>{html.escape(patientId)}</td></tr>
        <tr><th>Displayed-map alpha/beta</th><td>{displayAlphaBeta:.2f} Gy (highest selected)</td><th>Reported dose metrics</th><td>Mean, Dmax, D2cc</td></tr>
        <tr><th>Reference CT</th><td colspan="3">{html.escape(referenceCt.GetName() if referenceCt else 'Not selected')}</td></tr>
        <tr><th>Current RTSTRUCT</th><td colspan="3">{html.escape(currentStructures.GetName())}</td></tr>
        <tr><th>Registration method</th><td colspan="3">{html.escape(self.logic.registrationModeLabel(self._registrationModeKey()))}; previous-course registrations require visual QA</td></tr>
        <tr><th>Recovery model</th><td colspan="3">Paradis et al. 2025 Supplement E1; conservative stepwise boundaries; recovery capped at 0.50</td></tr>
        </table>
        <h2>Treatment courses</h2>
        <table><tr><th>#</th><th>Course</th><th>Role</th><th>Site</th><th>CT</th><th>RTDOSE</th><th>Registration</th><th>Fx</th><th>Months</th><th>Conservative map recovery</th></tr>
        {''.join(courseRows)}</table>
        <h2>Cumulative EQD2 organ metrics</h2>
        <table><tr><th>OAR</th><th>Alpha/beta (Gy)</th><th>Recovery by prior course</th><th>Volume (cc)</th><th>Mean</th><th>Dmax</th><th>D2cc</th></tr>
        {''.join(resultRows)}</table>
        <div class="dose-section">
        <h2>Current CT with cumulative EQD2 overlay</h2>
        <table class="projection-table"><tr>
        <td><div class="projection-label">Axial</div><img class="dose-image" width="165" src="{overlayImageUrls['Axial']}" /></td>
        <td><div class="projection-label">Sagittal</div><img class="dose-image" width="165" src="{overlayImageUrls['Sagittal']}" /></td>
        <td><div class="projection-label">Coronal</div><img class="dose-image" width="165" src="{overlayImageUrls['Coronal']}" /></td>
        </tr></table>
        <div class="caption">Orthogonal slices through the maximum-dose voxel. Display-map alpha/beta: {displayAlphaBeta:.2f} Gy (highest selected); range: 0-{maxDose:.2f} Gy EQD2; threshold: {thresholdDose:.2f} Gy EQD2 (5% of maximum); Cold to Hot Rainbow; foreground opacity: 45%.</div>
        </div>
        <div class="warning"><b>Research use only.</b> Registration, contours, dose mapping, structure-specific alpha/beta values, recovery assumptions, and DVH metrics require independent review by a radiation oncologist and medical physicist. This report is not a validated medical-device output.</div>
        </body></html>
        """
        try:
            document = qt.QTextDocument()
            document.setHtml(reportHtml)
            printer = qt.QPrinter(qt.QPrinter.HighResolution)
            printer.setOutputFormat(qt.QPrinter.PdfFormat)
            printer.setOutputFileName(os.path.abspath(savePath))
            printer.setPageSize(qt.QPageSize(qt.QPageSize.A4))
            printer.setPageMargins(qt.QMarginsF(12, 12, 12, 12), qt.QPageLayout.Millimeter)
            document.print_(printer)
        finally:
            for overlayImagePath in overlayImagePaths.values():
                try:
                    os.remove(overlayImagePath)
                except OSError:
                    pass

    def _captureDoseOverlaysForReport(self, referenceCt, doseNode):
        maxDose, thresholdDose = self.logic.prepareReportDoseDisplay(doseNode)
        layoutManager = slicer.app.layoutManager()
        layoutManager.setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
        slicer.util.setSliceViewerLayers(
            background=referenceCt,
            foreground=doseNode,
            foregroundOpacity=0.45,
            fit=True,
        )
        self.logic.setSlicesAtMaximumDose(doseNode)
        segmentationVisibility = []
        for segmentationNode in slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
            displayNode = segmentationNode.GetDisplayNode()
            if displayNode is not None:
                segmentationVisibility.append((displayNode, displayNode.GetVisibility()))
                displayNode.SetVisibility(False)
        colorLegend = None
        try:
            colorLegend = slicer.modules.colors.logic().AddDefaultColorLegendDisplayNode(doseNode)
            colorLegend.SetTitleText("Cumulative EQD2 (Gy)")
            colorLegend.SetVisibility(True)
        except Exception as exc:
            logging.warning("Color legend could not be added to report image: %s", exc)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        projectionViews = {"Axial": "Red", "Sagittal": "Yellow", "Coronal": "Green"}
        imagePaths = {
            projection: os.path.join(
                slicer.app.temporaryPath,
                f"ReRT_EQD2_{projection.lower()}_{timestamp}.png",
            )
            for projection in projectionViews
        }
        cornerTexts = {}
        for viewName in projectionViews.values():
            sliceView = layoutManager.sliceWidget(viewName).sliceView()
            cornerAnnotation = sliceView.cornerAnnotation()
            cornerTexts[viewName] = [cornerAnnotation.GetText(index) or "" for index in range(4)]
            for index in range(4):
                cornerAnnotation.SetText(index, "")
        try:
            slicer.app.processEvents()
            from ScreenCapture import ScreenCaptureLogic

            captureLogic = ScreenCaptureLogic()
            for projection, viewName in projectionViews.items():
                sliceView = layoutManager.sliceWidget(viewName).sliceView()
                captureLogic.captureImageFromView(
                    sliceView, imagePaths[projection], False
                )
        except Exception:
            for imagePath in imagePaths.values():
                try:
                    os.remove(imagePath)
                except OSError:
                    pass
            raise
        finally:
            for displayNode, visibility in segmentationVisibility:
                displayNode.SetVisibility(visibility)
            if colorLegend is not None:
                colorLegend.SetVisibility(False)
            for viewName, texts in cornerTexts.items():
                cornerAnnotation = layoutManager.sliceWidget(viewName).sliceView().cornerAnnotation()
                for index, value in enumerate(texts):
                    cornerAnnotation.SetText(index, value)
            slicer.app.processEvents()
        missingProjections = [
            projection
            for projection, imagePath in imagePaths.items()
            if not os.path.exists(imagePath)
        ]
        if missingProjections:
            for imagePath in imagePaths.values():
                try:
                    os.remove(imagePath)
                except OSError:
                    pass
            raise RuntimeError(
                "The CT/EQD2 overlay could not be captured for: "
                + ", ".join(missingProjections)
            )
        return imagePaths, maxDose, thresholdDose

    def _patientInfo(self, node):
        patientName = "Not available"
        patientId = "Not available"
        if node is None:
            return patientName, patientId
        try:
            shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
            itemId = shNode.GetItemByDataNode(node)
            while itemId:
                name = shNode.GetItemAttribute(itemId, "DICOM.PatientName")
                identifier = shNode.GetItemAttribute(itemId, "DICOM.PatientID")
                if name:
                    patientName = name.replace("^", " ").strip()
                if identifier:
                    patientId = identifier.strip()
                if name or identifier:
                    break
                itemId = shNode.GetItemParent(itemId)
        except Exception:
            logging.warning("Patient metadata could not be read from subject hierarchy.")
        return patientName, patientId


class SlicerReRTDoseAccumulatorLogic(ScriptedLoadableModuleLogic):
    @staticmethod
    def doseToEqd2(totalDoseGy, fractions, alphaBeta):
        if fractions <= 0:
            raise ValueError("Fractions must be positive.")
        if alphaBeta <= 0:
            raise ValueError("Alpha/beta must be positive.")
        dosePerFraction = totalDoseGy / float(fractions)
        bed = totalDoseGy * (1.0 + dosePerFraction / float(alphaBeta))
        return bed / (1.0 + 2.0 / float(alphaBeta))

    @staticmethod
    def tissueRecoveryFactor(segmentName, monthsSinceTreatment):
        """Conservative stepwise TRF based on Paradis et al. 2025 Supplement E1."""
        months = max(0.0, float(monthsSinceTreatment))
        normalized = "".join(character for character in segmentName.lower() if character.isalnum())

        isKidney = any(token in normalized for token in ("kidney", "renal", "rene"))
        isSmallBowel = any(
            token in normalized for token in ("bowelsmall", "smallbowel", "intestinotenue")
        )
        isDuodenum = "duoden" in normalized
        isStomach = any(token in normalized for token in ("stomach", "gastric", "stomaco"))
        isLung = any(token in normalized for token in ("lung", "polmon"))
        isLiver = any(token in normalized for token in ("liver", "hepatic", "fegat"))

        if isKidney:
            referenceFactor = 0.0 if months <= 36.0 else 0.5
        elif isSmallBowel or isStomach:
            if months <= 6.0:
                referenceFactor = 0.0
            elif months <= 12.0:
                referenceFactor = 0.25
            elif months <= 36.0:
                referenceFactor = 0.4
            else:
                referenceFactor = 0.5
        elif isDuodenum:
            if months <= 6.0:
                referenceFactor = 0.0
            elif months <= 36.0:
                referenceFactor = 0.25
            else:
                referenceFactor = 0.5
        elif isLung:
            if months <= 6.0:
                referenceFactor = 0.0
            elif months <= 24.0:
                referenceFactor = 0.25
            else:
                referenceFactor = 0.5
        elif isLiver:
            if months <= 6.0:
                referenceFactor = 0.0
            else:
                referenceFactor = 0.5
        else:
            if months <= 3.0:
                referenceFactor = 0.0
            elif months <= 6.0:
                referenceFactor = 0.1
            elif months <= 12.0:
                referenceFactor = 0.25
            else:
                referenceFactor = 0.5

        return min(referenceFactor, 0.5)

    def conservativeMapRecoveryFactor(self, segmentNames, monthsSinceTreatment):
        names = list(segmentNames) if segmentNames else [""]
        return min(self.tissueRecoveryFactor(name, monthsSinceTreatment) for name in names)

    def recoverySummary(self, mappedCourses, segmentName):
        factors = []
        for course in mappedCourses:
            if course.get("isReference"):
                continue
            recovery = self.tissueRecoveryFactor(segmentName, course["months"])
            factors.append(f"{course['name']}: {recovery:.2f}")
        return "; ".join(factors) if factors else "No previous course"

    @staticmethod
    def _hardenIfTransformed(node):
        if node is not None and node.GetParentTransformNode() is not None:
            logging.warning("Hardening parent transform on %s before CLI use.", node.GetName())
            slicer.vtkSlicerTransformLogic().hardenTransform(node)

    @staticmethod
    def _logVolumeStats(node, label):
        try:
            arr = slicer.util.arrayFromVolume(node)
            logging.info(
                "%s: shape=%s min=%.4g max=%.4g nonzero=%d/%d",
                label,
                arr.shape,
                float(arr.min()),
                float(arr.max()),
                int(np.count_nonzero(arr)),
                arr.size,
            )
        except Exception as exc:
            logging.warning("Could not compute stats for %s: %s", label, exc)

    def attachDerivedDoseToReferenceStudy(self, doseNode, referenceNode, unitName="Gy"):
        if doseNode is None or referenceNode is None:
            raise ValueError("A derived dose and reference CT are required.")
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        referenceItemId = shNode.GetItemByDataNode(referenceNode)
        sceneItemId = shNode.GetSceneItemID()
        studyLevel = slicer.vtkMRMLSubjectHierarchyConstants.GetDICOMLevelStudy()
        patientLevel = slicer.vtkMRMLSubjectHierarchyConstants.GetDICOMLevelPatient()

        studyItemId = 0
        patientItemId = 0
        itemId = referenceItemId
        while itemId and itemId != sceneItemId:
            itemLevel = shNode.GetItemLevel(itemId)
            if itemLevel == studyLevel and not studyItemId:
                studyItemId = itemId
            if itemLevel == patientLevel and not patientItemId:
                patientItemId = itemId
            itemId = shNode.GetItemParent(itemId)

        if not studyItemId:
            if not patientItemId:
                patientItemId = shNode.CreateSubjectItem(
                    sceneItemId, f"{referenceNode.GetName()} patient"
                )
            studyItemId = shNode.CreateStudyItem(
                patientItemId, f"{referenceNode.GetName()} reirradiation results"
            )

        doseItemId = shNode.GetItemByDataNode(doseNode)
        if not doseItemId:
            doseItemId = shNode.CreateItem(studyItemId, doseNode)
        else:
            shNode.SetItemParent(doseItemId, studyItemId)

        doseNode.SetAttribute("DicomRtImport.DoseVolume", "1")
        doseNode.SetAttribute("DicomRtImport.DoseUnitName", str(unitName))
        doseNode.SetAttribute("DicomRtImport.DoseUnitValue", "1.0")
        shNode.SetItemAttribute(doseItemId, "DoseUnitName", str(unitName))
        shNode.SetItemAttribute(doseItemId, "DoseUnitValue", "1.0")
        if not shNode.GetItemAttribute(studyItemId, "DicomRtImport.DoseUnitName"):
            shNode.SetItemAttribute(studyItemId, "DicomRtImport.DoseUnitName", "Gy")
            shNode.SetItemAttribute(studyItemId, "DicomRtImport.DoseUnitValue", "1.0")
        shNode.RequestOwnerPluginSearch(doseItemId)
        shNode.ItemModified(doseItemId)
        return studyItemId

    @staticmethod
    def registrationModeLabel(registrationMode):
        return (
            "Rigid + affine + deformable B-spline"
            if registrationMode == "deformable"
            else "Rigid"
        )

    def mapCoursesToLatestCt(self, courses, registrationMode="rigid"):
        if len(courses) < 1:
            raise ValueError("Add at least one treatment.")
        if registrationMode not in ("rigid", "deformable"):
            raise ValueError(f"Unsupported registration mode: {registrationMode}")
        referenceCourses = [course for course in courses if course.get("isReference")]
        if len(referenceCourses) != 1:
            raise ValueError("Select exactly one CURRENT / REFERENCE treatment.")
        referenceCourse = referenceCourses[0]
        referenceCt = referenceCourse["ct"]
        if referenceCt is None:
            raise ValueError("The CURRENT / REFERENCE treatment must have a CT selected.")

        mappedCourses = []
        for course in courses:
            if course["dose"] is None:
                raise ValueError(f"Select RT dose for treatment: {course['name']}")
            if course["fractions"] <= 0:
                raise ValueError(f"Fractions must be positive for treatment: {course['name']}")
            if course.get("isReference"):
                mappedDose = self.resampleDoseToReference(
                    doseNode=course["dose"],
                    referenceNode=referenceCt,
                    outputName=f"{course['dose'].GetName()}_on_latest_CT",
                )
                mappedDose.SetAttribute("ReRT.RegistrationMode", "reference")
                mappedCourses.append(
                    {
                        **course,
                        "dose": mappedDose,
                        "structures": course["structures"],
                        "registeredCt": referenceCt,
                        "transform": None,
                        "registrationMode": "reference",
                    }
                )
                continue
            if course["ct"] is None:
                raise ValueError(f"Select CT for previous treatment: {course['name']}")
            mappedDose, registeredCt, mappedStructures, transformNode = self.registerDoseAndStructures(
                fixedCtNode=referenceCt,
                movingCtNode=course["ct"],
                doseNode=course["dose"],
                segmentationNode=course["structures"],
                label=course["name"],
                registrationMode=registrationMode,
            )
            mappedCourses.append(
                {
                    **course,
                    "dose": mappedDose,
                    "structures": mappedStructures,
                    "registeredCt": registeredCt,
                    "transform": transformNode,
                    "registrationMode": registrationMode,
                }
            )
        return mappedCourses, referenceCt

    def ensureCoursesAreOnLatestCt(self, courses, registrationMode="rigid"):
        alreadyMapped = []
        referenceCourses = [course for course in courses if course.get("isReference")]
        if len(referenceCourses) != 1:
            raise ValueError("Select exactly one CURRENT / REFERENCE treatment.")
        referenceCt = referenceCourses[0]["ct"]
        if referenceCt is None:
            raise ValueError("The CURRENT / REFERENCE treatment must have a CT selected.")
        for course in courses:
            mappedDose = course["mappedDoseSelector"].currentNode()
            mappedStructures = course["mappedStructuresSelector"].currentNode()
            registeredCt = course["registeredCtSelector"].currentNode()
            transformNode = course["registrationTransformSelector"].currentNode()
            expectedMode = "reference" if course.get("isReference") else registrationMode
            mappedMode = mappedDose.GetAttribute("ReRT.RegistrationMode") if mappedDose else None
            if mappedDose is None or mappedMode != expectedMode:
                return self.mapCoursesToLatestCt(courses, registrationMode)
            alreadyMapped.append(
                {
                    **course,
                    "dose": mappedDose,
                    "structures": mappedStructures if mappedStructures is not None else course["structures"],
                    "registeredCt": registeredCt if registeredCt is not None else referenceCt,
                    "transform": transformNode,
                    "registrationMode": expectedMode,
                }
            )
        return alreadyMapped, referenceCt

    def registerDoseAndStructures(
        self,
        fixedCtNode,
        movingCtNode,
        doseNode,
        segmentationNode,
        label,
        registrationMode="rigid",
    ):
        self._logVolumeStats(doseNode, f"{label} dose before mapping")
        self._hardenIfTransformed(fixedCtNode)
        self._hardenIfTransformed(movingCtNode)
        self._hardenIfTransformed(doseNode)

        deformable = registrationMode == "deformable"
        # BRAINSFit may return a composite transform when linear and non-linear
        # stages are combined, so the deformable output must use a generic node.
        transformClass = (
            "vtkMRMLTransformNode" if deformable else "vtkMRMLLinearTransformNode"
        )
        transformSuffix = "rigid_bspline" if deformable else "rigid"
        transformNode = slicer.mrmlScene.AddNewNodeByClass(
            transformClass,
            f"{movingCtNode.GetName()}_to_{fixedCtNode.GetName()}_{transformSuffix}",
        )
        registeredCtNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLScalarVolumeNode",
            f"{movingCtNode.GetName()}_{transformSuffix}_registered_to_{fixedCtNode.GetName()}",
        )
        parameters = {
            "fixedVolume": fixedCtNode.GetID(),
            "movingVolume": movingCtNode.GetID(),
            "outputVolume": registeredCtNode.GetID(),
            "initializeTransformMode": "useMomentsAlign",
            "interpolationMode": "Linear",
        }
        if deformable:
            # Use the same reliable rigid registration as the rigid-only mode,
            # then pass it explicitly to the affine and B-spline stages. Running
            # all stages in one CLI call makes the rigid stage inherit the
            # deformable masking/sampling settings and may prevent convergence.
            rigidInitializationNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLinearTransformNode",
                f"{movingCtNode.GetName()}_to_{fixedCtNode.GetName()}_rigid_initialization",
            )
            rigidParameters = {
                "fixedVolume": fixedCtNode.GetID(),
                "movingVolume": movingCtNode.GetID(),
                "linearTransform": rigidInitializationNode.GetID(),
                "initializeTransformMode": "useMomentsAlign",
                "useRigid": True,
            }
            rigidCliNode = slicer.cli.runSync(
                slicer.modules.brainsfit, None, rigidParameters
            )
            if rigidCliNode.GetStatus() & rigidCliNode.ErrorsMask:
                rigidError = (
                    rigidCliNode.GetErrorText()
                    if hasattr(rigidCliNode, "GetErrorText")
                    else ""
                )
                rigidOutput = (
                    rigidCliNode.GetOutputText()
                    if hasattr(rigidCliNode, "GetOutputText")
                    else ""
                )
                rigidDetails = "\n".join(
                    text for text in (rigidError, rigidOutput) if text
                ).strip()
                if rigidDetails:
                    logging.error(rigidDetails)
                    rigidDetails = "\n".join(rigidDetails.splitlines()[-40:])
                raise RuntimeError(
                    f"BRAINSFit rigid initialization failed for {label} "
                    f"(status={rigidCliNode.GetStatusString()})."
                    f"{chr(10) + rigidDetails if rigidDetails else ''}"
                )
            parameters.update(
                {
                    "initialTransform": rigidInitializationNode.GetID(),
                    "initializeTransformMode": "Off",
                    "bsplineTransform": transformNode.GetID(),
                    "useAffine": True,
                    "useBSpline": True,
                    "maskProcessingMode": "ROIAUTO",
                    "ROIAutoDilateSize": 10.0,
                    "samplingPercentage": 0.002,
                    "removeIntensityOutliers": 0.005,
                    "splineGridSize": [8, 8, 8],
                    "numberOfIterations": [1500, 3000],
                    "maximumNumberOfEvaluations": 3000,
                    "projectedGradientTolerance": 0.0001,
                    "maxBSplineDisplacement": 20.0,
                }
            )
        else:
            parameters.update(
                {
                    "linearTransform": transformNode.GetID(),
                    "useRigid": True,
                }
            )
        cliNode = slicer.cli.runSync(slicer.modules.brainsfit, None, parameters)
        if cliNode.GetStatus() & cliNode.ErrorsMask:
            errorText = cliNode.GetErrorText() if hasattr(cliNode, "GetErrorText") else ""
            outputText = cliNode.GetOutputText() if hasattr(cliNode, "GetOutputText") else ""
            details = "\n".join(text for text in (errorText, outputText) if text).strip()
            if details:
                logging.error(details)
                details = "\n".join(details.splitlines()[-40:])
            raise RuntimeError(
                f"BRAINSFit {self.registrationModeLabel(registrationMode)} registration failed for "
                f"{label} (status={cliNode.GetStatusString()})."
                f"{chr(10) + details if details else ''}"
            )

        if deformable:
            if transformNode.IsLinear():
                raise RuntimeError(
                    f"BRAINSFit completed for {label}, but the resulting transform is linear. "
                    "The B-spline stage was not created."
                )
            transformNode.SetAttribute("ReRT.RequiresRegistrationQA", "1")
        else:
            self._warnIfLargeTranslation(transformNode, registeredCtNode, fixedCtNode)
        transformNode.SetAttribute("ReRT.RegistrationMode", registrationMode)

        outputDoseNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLScalarVolumeNode",
            f"{doseNode.GetName()}_{transformSuffix}_mapped_to_{fixedCtNode.GetName()}",
        )
        resampleParameters = {
            "inputVolume": doseNode.GetID(),
            "referenceVolume": fixedCtNode.GetID(),
            "outputVolume": outputDoseNode.GetID(),
            "transformationFile": transformNode.GetID(),
            "interpolationType": "linear",
            "defaultPixelValue": 0.0,
        }
        resampleCliNode = slicer.cli.runSync(
            slicer.modules.resamplescalarvectordwivolume, None, resampleParameters
        )
        if resampleCliNode.GetStatus() & resampleCliNode.ErrorsMask:
            errorText = resampleCliNode.GetErrorText() if hasattr(resampleCliNode, "GetErrorText") else ""
            raise RuntimeError(
                f"Dose resampling failed for {label} (status={resampleCliNode.GetStatusString()}). {errorText}"
            )

        mappedStructures = None
        if segmentationNode is not None:
            mappedStructures = self.cloneAndTransformSegmentation(
                segmentationNode,
                transformNode,
                fixedCtNode,
                f"{segmentationNode.GetName()}_{transformSuffix}_mapped_to_{fixedCtNode.GetName()}",
            )
        outputDoseNode.SetAttribute("ReRT.RegistrationMode", registrationMode)
        registeredCtNode.SetAttribute("ReRT.RegistrationMode", registrationMode)
        if mappedStructures is not None:
            mappedStructures.SetAttribute("ReRT.RegistrationMode", registrationMode)
        self._logVolumeStats(outputDoseNode, f"{label} mapped dose")
        return outputDoseNode, registeredCtNode, mappedStructures, transformNode

    def rigidRegisterDoseAndStructures(
        self, fixedCtNode, movingCtNode, doseNode, segmentationNode, label
    ):
        mappedDose, registeredCt, mappedStructures, _ = self.registerDoseAndStructures(
            fixedCtNode,
            movingCtNode,
            doseNode,
            segmentationNode,
            label,
            "rigid",
        )
        return mappedDose, registeredCt, mappedStructures

    def _warnIfLargeTranslation(self, transformNode, registeredCtNode, fixedCtNode):
        matrix = vtk.vtkMatrix4x4()
        transformNode.GetMatrixTransformToParent(matrix)
        translation = (matrix.GetElement(0, 3), matrix.GetElement(1, 3), matrix.GetElement(2, 3))
        logging.info("Rigid transform translation (mm): %s", translation)
        if max(abs(t) for t in translation) > 150:
            logging.warning(
                "Rigid registration translation is very large (%s mm). Inspect '%s' against '%s'.",
                translation,
                registeredCtNode.GetName(),
                fixedCtNode.GetName(),
            )

    def cloneAndTransformSegmentation(self, segmentationNode, transformNode, referenceCtNode, outputName):
        mappedNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", outputName)
        mappedNode.Copy(segmentationNode)
        mappedNode.SetName(outputName)
        mappedNode.SetReferenceImageGeometryParameterFromVolumeNode(referenceCtNode)
        mappedNode.SetAndObserveTransformNodeID(transformNode.GetID())
        slicer.vtkSlicerTransformLogic().hardenTransform(mappedNode)
        mappedNode.CreateDefaultDisplayNodes()
        displayNode = mappedNode.GetDisplayNode()
        if displayNode is not None:
            displayNode.SetVisibility2DFill(True)
            displayNode.SetVisibility2DOutline(True)
            displayNode.SetOpacity(0.35)
        return mappedNode

    def resampleDoseToReference(self, doseNode, referenceNode, outputName):
        if doseNode is None or referenceNode is None:
            raise ValueError("Dose and reference CT are required for resampling.")
        self._hardenIfTransformed(doseNode)
        identityTransform = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLinearTransformNode",
            f"{doseNode.GetName()}_identity_resample",
        )
        outputDoseNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLScalarVolumeNode",
            outputName or f"{doseNode.GetName()}_resampled",
        )
        parameters = {
            "inputVolume": doseNode.GetID(),
            "referenceVolume": referenceNode.GetID(),
            "outputVolume": outputDoseNode.GetID(),
            "transformationFile": identityTransform.GetID(),
            "interpolationType": "linear",
            "defaultPixelValue": 0.0,
        }
        cliNode = slicer.cli.runSync(
            slicer.modules.resamplescalarvectordwivolume, None, parameters
        )
        if cliNode.GetStatus() & cliNode.ErrorsMask:
            errorText = cliNode.GetErrorText() if hasattr(cliNode, "GetErrorText") else ""
            raise RuntimeError(
                f"Dose resampling failed (status={cliNode.GetStatusString()}). {errorText}"
            )
        self._logVolumeStats(outputDoseNode, "dose after reference resampling")
        return outputDoseNode

    def computeMultiCourseDose(self, mappedCourses, alphaBeta, physicalOutputName, eqd2OutputName):
        if not mappedCourses:
            raise ValueError("No mapped treatments available.")
        referenceCourse = next(
            (course for course in mappedCourses if course.get("isReference")), None
        )
        if referenceCourse is None:
            raise ValueError("The mapped courses do not contain a CURRENT / REFERENCE treatment.")
        referenceDoseNode = referenceCourse["dose"]
        referenceShape = slicer.util.arrayFromVolume(referenceDoseNode).shape
        totalPhysical = np.zeros(referenceShape, dtype=np.float32)
        totalEqd2 = np.zeros(referenceShape, dtype=np.float32)

        for course in mappedCourses:
            doseArray = slicer.util.arrayFromVolume(course["dose"]).astype(np.float32)
            if doseArray.shape != referenceShape:
                raise ValueError(
                    f"Dose grid for {course['name']} does not match the reference grid. Re-map the course."
                )
            eqd2 = self.doseToEqd2(doseArray, course["fractions"], alphaBeta)
            discount = 1.0 - float(course["recovery"])
            if course.get("isReference"):
                discount = 1.0
            totalPhysical += doseArray
            totalEqd2 += eqd2 * discount

        physicalNode = slicer.modules.volumes.logic().CloneVolume(
            slicer.mrmlScene,
            referenceDoseNode,
            physicalOutputName or "Total_Physical_Dose_all_courses",
        )
        slicer.util.updateVolumeFromArray(physicalNode, totalPhysical.astype(np.float32))
        physicalNode.SetAttribute("DoseUnitName", "Gy")
        physicalNode.SetAttribute("ReRT.VolumeType", "TotalPhysicalDose")

        eqd2Node = slicer.modules.volumes.logic().CloneVolume(
            slicer.mrmlScene,
            referenceDoseNode,
            eqd2OutputName or "Cumulative_EQD2_all_courses",
        )
        slicer.util.updateVolumeFromArray(eqd2Node, totalEqd2.astype(np.float32))
        eqd2Node.SetAttribute("DoseUnitName", "Gy_EQD2")
        eqd2Node.SetAttribute("ReRT.AlphaBeta", str(alphaBeta))
        eqd2Node.SetAttribute(
            "ReRT.RecoveryModel", "Paradis2025_SupplementE1_conservative_stepwise"
        )
        eqd2Node.SetAttribute("ReRT.RecoveryCap", "0.5")
        eqd2Node.SetAttribute("ReRT.VolumeType", "CumulativeEQD2")
        return physicalNode, eqd2Node

    @staticmethod
    def doseToHottestVolume(values, voxelVolumeCc, requestedVolumeCc):
        if values.size == 0:
            return float("nan")
        voxelCount = max(1, int(np.ceil(float(requestedVolumeCc) / voxelVolumeCc)))
        voxelCount = min(voxelCount, values.size)
        descending = np.sort(values)[::-1]
        return float(descending[voxelCount - 1])

    def cumulativeEqd2Array(self, mappedCourses, alphaBeta, segmentName=None):
        if not mappedCourses:
            raise ValueError("No mapped treatments available.")
        referenceCourse = next(
            (course for course in mappedCourses if course.get("isReference")), None
        )
        if referenceCourse is None:
            raise ValueError("No CURRENT / REFERENCE treatment is available.")
        referenceShape = slicer.util.arrayFromVolume(referenceCourse["dose"]).shape
        cumulativeEqd2 = np.zeros(referenceShape, dtype=np.float32)
        for course in mappedCourses:
            doseArray = slicer.util.arrayFromVolume(course["dose"]).astype(np.float32)
            if doseArray.shape != referenceShape:
                raise ValueError(
                    f"Dose grid for {course['name']} does not match the reference grid."
                )
            if course.get("isReference"):
                recoveryDiscount = 1.0
            else:
                recoveryFactor = (
                    self.tissueRecoveryFactor(segmentName, course["months"])
                    if segmentName is not None
                    else float(course["recovery"])
                )
                recoveryDiscount = 1.0 - recoveryFactor
            cumulativeEqd2 += (
                self.doseToEqd2(doseArray, course["fractions"], alphaBeta)
                * recoveryDiscount
            )
        return cumulativeEqd2

    @staticmethod
    def _segmentIdsByName(segmentationNode):
        segmentation = segmentationNode.GetSegmentation()
        return {
            segmentation.GetNthSegment(index).GetName().strip().lower():
                segmentation.GetNthSegmentID(index)
            for index in range(segmentation.GetNumberOfSegments())
        }

    def computeOarSpecificDoseStats(self, segmentationNode, mappedCourses, oarSettings):
        if segmentationNode is None:
            return []
        referenceCourse = next(
            (course for course in mappedCourses if course.get("isReference")), None
        )
        if referenceCourse is None:
            raise ValueError("No CURRENT / REFERENCE treatment is available.")
        referenceDoseNode = referenceCourse["dose"]
        segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(referenceDoseNode)
        spacing = referenceDoseNode.GetSpacing()
        voxelVolumeCc = float(spacing[0] * spacing[1] * spacing[2]) / 1000.0
        segmentIds = self._segmentIdsByName(segmentationNode)
        eqd2Arrays = {}
        stats = []
        for setting in oarSettings:
            segmentName = setting["segmentName"]
            alphaBeta = float(setting["alphaBeta"])
            segmentId = segmentIds.get(segmentName.strip().lower())
            if segmentId is None:
                logging.warning("Structure '%s' is no longer present in the RTSTRUCT.", segmentName)
                continue
            arrayKey = (alphaBeta, segmentName.strip().lower())
            if arrayKey not in eqd2Arrays:
                eqd2Arrays[arrayKey] = self.cumulativeEqd2Array(
                    mappedCourses, alphaBeta, segmentName
                )
            doseArray = eqd2Arrays[arrayKey]
            segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                segmentationNode, segmentId, referenceDoseNode
            )
            if segmentArray is None or segmentArray.shape != doseArray.shape:
                logging.warning("Structure '%s' does not match the dose grid.", segmentName)
                continue
            values = doseArray[segmentArray > 0]
            if values.size == 0:
                continue
            stats.append(
                {
                    "segmentName": segmentName,
                    "alphaBeta": alphaBeta,
                    "recoverySummary": self.recoverySummary(mappedCourses, segmentName),
                    "volumeCc": float(values.size * voxelVolumeCc),
                    "mean": float(np.mean(values)),
                    "max": float(np.max(values)),
                    "d2cc": self.doseToHottestVolume(values, voxelVolumeCc, 2.0),
                }
            )
        return stats

    def createOarSpecificDvhPlot(
        self,
        segmentationNode,
        mappedCourses,
        oarSettings,
        absoluteVolume=False,
        visibleSegmentsOnly=True,
    ):
        if segmentationNode is None:
            raise ValueError("A current-treatment RTSTRUCT is required for DVH generation.")
        for nodeClass in ("vtkMRMLPlotChartNode", "vtkMRMLPlotSeriesNode", "vtkMRMLTableNode"):
            for node in list(slicer.util.getNodesByClass(nodeClass)):
                if node.GetName().startswith("ReRT_DVH_"):
                    slicer.mrmlScene.RemoveNode(node)

        referenceCourse = next(
            (course for course in mappedCourses if course.get("isReference")), None
        )
        if referenceCourse is None:
            raise ValueError("No CURRENT / REFERENCE treatment is available.")
        referenceDoseNode = referenceCourse["dose"]
        segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(referenceDoseNode)
        spacing = referenceDoseNode.GetSpacing()
        voxelVolumeCc = float(spacing[0] * spacing[1] * spacing[2]) / 1000.0
        segmentation = segmentationNode.GetSegmentation()
        segmentIds = self._segmentIdsByName(segmentationNode)
        segmentationDisplayNode = segmentationNode.GetDisplayNode()
        chartNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLPlotChartNode", "ReRT_DVH_OAR_Specific_EQD2"
        )
        chartNode.SetTitle("Cumulative EQD2 DVH - structure-specific alpha/beta")
        chartNode.SetXAxisTitle("Dose EQD2 (Gy)")
        chartNode.SetYAxisTitle("Absolute volume (cc)" if absoluteVolume else "Relative volume (%)")
        maximumDisplayedDose = 0.0
        curveCount = 0
        eqd2Arrays = {}

        for setting in oarSettings:
            segmentName = setting["segmentName"]
            alphaBeta = float(setting["alphaBeta"])
            segmentId = segmentIds.get(segmentName.strip().lower())
            if segmentId is None:
                continue
            segment = segmentation.GetSegment(segmentId)
            if (
                visibleSegmentsOnly
                and segmentationDisplayNode is not None
                and not segmentationDisplayNode.GetSegmentVisibility(segmentId)
            ):
                continue
            arrayKey = (alphaBeta, segmentName.strip().lower())
            if arrayKey not in eqd2Arrays:
                eqd2Arrays[arrayKey] = self.cumulativeEqd2Array(
                    mappedCourses, alphaBeta, segmentName
                )
            doseArray = eqd2Arrays[arrayKey]
            segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                segmentationNode, segmentId, referenceDoseNode
            )
            if segmentArray is None or segmentArray.shape != doseArray.shape:
                continue
            values = doseArray[segmentArray > 0]
            if values.size == 0:
                continue
            upperDose = float(np.percentile(values, 99.9))
            upperDose = max(upperDose, 0.1)
            maximumDisplayedDose = max(maximumDisplayedDose, upperDose)
            doseBins = np.linspace(0.0, upperDose, 241)
            sortedValues = np.sort(values)
            firstAbove = np.searchsorted(sortedValues, doseBins, side="left")
            cumulativeVoxels = values.size - firstAbove
            volumes = (
                cumulativeVoxels * voxelVolumeCc
                if absoluteVolume
                else cumulativeVoxels.astype(np.float64) * 100.0 / values.size
            )

            tableNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLTableNode",
                f"ReRT_DVH_Data_{segment.GetName()}_ab_{alphaBeta:g}",
            )
            doseColumn = vtk.vtkDoubleArray()
            doseColumn.SetName("Dose_EQD2_Gy")
            volumeColumn = vtk.vtkDoubleArray()
            volumeColumn.SetName("Volume_cc" if absoluteVolume else "Volume_percent")
            for dose, volume in zip(doseBins, volumes):
                doseColumn.InsertNextValue(float(dose))
                volumeColumn.InsertNextValue(float(volume))
            tableNode.AddColumn(doseColumn)
            tableNode.AddColumn(volumeColumn)

            seriesNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLPlotSeriesNode",
                f"ReRT_DVH_Series_{segment.GetName()} (a/b={alphaBeta:g} Gy)",
            )
            seriesNode.SetAndObserveTableNodeID(tableNode.GetID())
            seriesNode.SetXColumnName("Dose_EQD2_Gy")
            seriesNode.SetYColumnName("Volume_cc" if absoluteVolume else "Volume_percent")
            seriesNode.SetPlotType(slicer.vtkMRMLPlotSeriesNode.PlotTypeScatter)
            seriesNode.SetMarkerStyle(slicer.vtkMRMLPlotSeriesNode.MarkerStyleNone)
            color = segment.GetColor()
            seriesNode.SetColor(color[0], color[1], color[2])
            chartNode.AddAndObservePlotSeriesNodeID(seriesNode.GetID())
            curveCount += 1

        if curveCount == 0:
            slicer.mrmlScene.RemoveNode(chartNode)
            raise ValueError("No non-empty structures were available for DVH generation.")
        chartNode.SetXAxisRangeAuto(False)
        chartNode.SetXAxisRange(0.0, maximumDisplayedDose * 1.1)
        if not absoluteVolume:
            chartNode.SetYAxisRangeAuto(False)
            chartNode.SetYAxisRange(0.0, 105.0)
        layoutManager = slicer.app.layoutManager()
        layoutManager.setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpPlotView)
        plotWidget = layoutManager.plotWidget(0)
        plotWidget.mrmlPlotViewNode().SetPlotChartNodeID(chartNode.GetID())
        return chartNode

    def showDoseOnCt(self, ctNode, doseNode, opacity=0.45, showVolumeRendering=True):
        if ctNode is None:
            raise ValueError("Select the CT where the dose should be displayed.")
        if doseNode is None:
            raise ValueError("Select a dose volume to display.")

        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
        self._prepareDoseDisplay(doseNode)
        slicer.util.setSliceViewerLayers(
            background=ctNode,
            foreground=doseNode,
            foregroundOpacity=float(opacity),
            fit=True,
        )
        self._centerSlices()
        if showVolumeRendering:
            self._showDoseVolumeRendering(doseNode)

    def prepareReportDoseDisplay(self, doseNode):
        if doseNode is None:
            raise ValueError("A cumulative EQD2 dose is required for report imaging.")
        doseNode.CreateDefaultDisplayNodes()
        displayNode = doseNode.GetDisplayNode()
        doseArray = slicer.util.arrayFromVolume(doseNode).astype(np.float32)
        maxDose = float(np.max(doseArray))
        if maxDose <= 0.0:
            raise ValueError("The cumulative EQD2 volume contains no positive dose.")
        thresholdDose = 0.05 * maxDose
        colorNode = slicer.mrmlScene.GetNodeByID(
            "vtkMRMLColorTableNodeFileColdToHotRainbow.txt"
        )
        if colorNode is None:
            colorNode = slicer.mrmlScene.GetNodeByID("vtkMRMLColorTableNodeRainbow")
        if colorNode is not None:
            displayNode.SetAndObserveColorNodeID(colorNode.GetID())
        displayNode.SetAutoWindowLevel(False)
        displayNode.SetWindow(maxDose)
        displayNode.SetLevel(maxDose / 2.0)
        if hasattr(displayNode, "AutoThresholdOff"):
            displayNode.AutoThresholdOff()
        displayNode.SetLowerThreshold(thresholdDose)
        displayNode.SetUpperThreshold(maxDose)
        displayNode.SetApplyThreshold(True)
        return maxDose, thresholdDose

    def setSlicesAtMaximumDose(self, doseNode):
        doseArray = slicer.util.arrayFromVolume(doseNode)
        maxKji = np.unravel_index(int(np.argmax(doseArray)), doseArray.shape)
        ijkPoint = [float(maxKji[2]), float(maxKji[1]), float(maxKji[0]), 1.0]
        ijkToRas = vtk.vtkMatrix4x4()
        doseNode.GetIJKToRASMatrix(ijkToRas)
        rasPoint = ijkToRas.MultiplyPoint(ijkPoint)
        sliceSettings = (
            ("Red", "Axial", rasPoint[2]),
            ("Yellow", "Sagittal", rasPoint[0]),
            ("Green", "Coronal", rasPoint[1]),
        )
        layoutManager = slicer.app.layoutManager()
        for viewName, orientation, offset in sliceSettings:
            sliceWidget = layoutManager.sliceWidget(viewName)
            sliceWidget.mrmlSliceNode().SetOrientation(orientation)
            sliceWidget.sliceLogic().SetSliceOffset(offset)
            sliceWidget.sliceLogic().FitSliceToAll()

    def _prepareDoseDisplay(self, doseNode):
        doseNode.CreateDefaultDisplayNodes()
        displayNode = doseNode.GetDisplayNode()
        if displayNode is None:
            return
        colorNode = slicer.mrmlScene.GetNodeByID("vtkMRMLColorTableNodeFileHotToColdRainbow.txt")
        if colorNode is None:
            colorNode = slicer.mrmlScene.GetNodeByID("vtkMRMLColorTableNodeRainbow")
        if colorNode is not None:
            displayNode.SetAndObserveColorNodeID(colorNode.GetID())
        doseArray = slicer.util.arrayFromVolume(doseNode)
        positiveDose = doseArray[doseArray > 0]
        if positiveDose.size:
            low = float(np.percentile(positiveDose, 5))
            high = float(np.percentile(positiveDose, 98))
            if high > low:
                displayNode.SetAutoWindowLevel(False)
                displayNode.SetWindow(high - low)
                displayNode.SetLevel((high + low) / 2.0)

    def _centerSlices(self):
        layoutManager = slicer.app.layoutManager()
        for sliceViewName in ("Red", "Yellow", "Green"):
            sliceWidget = layoutManager.sliceWidget(sliceViewName)
            if sliceWidget:
                sliceWidget.sliceLogic().FitSliceToAll()

    def _showDoseVolumeRendering(self, doseNode):
        try:
            if not hasattr(slicer.modules, "volumerendering"):
                return
            volumeRenderingLogic = slicer.modules.volumerendering.logic()
            displayNode = volumeRenderingLogic.GetFirstVolumeRenderingDisplayNode(doseNode)
            if displayNode is None:
                displayNode = volumeRenderingLogic.CreateDefaultVolumeRenderingNodes(doseNode)
            if displayNode is None:
                return
            displayNode.SetVisibility(True)
            doseArray = slicer.util.arrayFromVolume(doseNode)
            positiveDose = doseArray[doseArray > 0]
            if positiveDose.size and hasattr(displayNode, "SetThreshold"):
                threshold = float(np.percentile(positiveDose, 80))
                displayNode.SetThreshold(threshold, float(positiveDose.max()))
        except Exception as exc:
            logging.warning("Dose volume rendering was not enabled: %s", exc)
