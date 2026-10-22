# SlicerReRTDoseAccumulator

SlicerReRTDoseAccumulator is a scripted 3D Slicer extension for research-oriented analysis of patients who have received multiple radiotherapy courses.

The extension maps previous treatment courses to the current planning CT, calculates cumulative physical dose and EQD2, extracts organ-at-risk metrics, displays cumulative dose and DVHs, and generates a PDF report.

> **Research use only.** This extension is not a medical device and is not validated for independent clinical decision-making. Registration, transformed contours, mapped doses, radiobiological parameters, recovery assumptions, DVHs, and reported metrics require review by qualified radiation oncology and medical physics professionals.

## Features

- Any number of previous radiotherapy courses.
- Explicit selection of the current/reference treatment.
- Rigid or rigid plus deformable B-spline registration.
- Mapping of previous CT, RTDOSE, and RTSTRUCT data to the current CT.
- Cumulative physical-dose calculation.
- BED/EQD2 calculation with editable structure-specific alpha/beta values.
- Conservative time-dependent recovery factors.
- OAR-specific Dmean, Dmax, and D2cc metrics.
- Cumulative DVH generation.
- CT display with cumulative EQD2 overlay.
- PDF report with axial, sagittal, and coronal dose screenshots.

## Requirements

- 3D Slicer 5.12 or newer.
- SlicerRT extension.
- DICOM RT data imported through Slicer's DICOM browser.

BRAINSFit, included in 3D Slicer, is used for rigid and B-spline registration.

## Installation from the Extension Manager

After the extension is accepted into the official Slicer Extensions Index:

1. Open 3D Slicer.
2. Select **View > Extensions Manager**.
3. Search for **ReRT Dose Accumulator**.
4. Select **Install** and restart Slicer.

## Installation from source

1. Clone or download this repository.
2. Open **Developer Tools > Extension Wizard** in 3D Slicer.
3. Select the repository folder as an existing extension.
4. Load `SlicerReRTDoseAccumulator`.

For development, the module directory can also be added under **Application Settings > Modules > Additional module paths**:

```text
SlicerReRTDoseAccumulator/ReRTDoseAccumulator
```

Restart Slicer after changing additional module paths.

## Basic workflow

1. Import every CT, RTDOSE, and RTSTRUCT using the DICOM browser.
2. Add the required treatment courses.
3. Mark the most recent treatment as **CURRENT / REFERENCE**.
4. Select rigid or deformable registration.
5. Register previous treatments to the current CT.
6. Review every registered CT, transformed structure, and registration transform.
7. Select OARs and verify their alpha/beta values.
8. Calculate cumulative physical dose and EQD2.
9. Review DVHs and export the PDF report.

## Deformable registration

The deformable option performs rigid pre-alignment followed by B-spline registration. The resulting transform is applied consistently to the previous dose and segmentation.

Deformable dose accumulation is sensitive to registration quality and anatomical changes. Particular caution is required after surgery, with new or resolved cavities, major tumor changes, implants, severe artifacts, or incomplete image coverage.

## Data privacy

Do not commit patient images, DICOM files, reports, logs, screenshots containing identifiers, or exported scene files to this repository.

## Acknowledgements

This extension is built for the 3D Slicer and SlicerRT ecosystems.
