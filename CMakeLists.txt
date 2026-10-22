cmake_minimum_required(VERSION 3.28.0...3.28.0 FATAL_ERROR)

project(SlicerReRTDoseAccumulator)

# -----------------------------------------------------------------------------
# Extension metadata
set(EXTENSION_HOMEPAGE "")
set(EXTENSION_CONTRIBUTORS "ReRT Dose Accumulator contributors")
set(EXTENSION_DESCRIPTION "Multi-course radiotherapy dose mapping, physical-dose and EQD2 accumulation, OAR DVH analysis, and PDF reporting for reirradiation research workflows.")
set(EXTENSION_ICONURL "")
set(EXTENSION_SCREENSHOTURLS "")
set(EXTENSION_DEPENDS "SlicerRT")

# -----------------------------------------------------------------------------
# Extension dependencies
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

# -----------------------------------------------------------------------------
# Extension modules
add_subdirectory(ReRTDoseAccumulator)

# -----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
