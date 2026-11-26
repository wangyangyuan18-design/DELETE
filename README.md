# DELETE HUB BOX (QGIS Plugin)

A QGIS plugin to retain selected features in the `HUB BOX` layer and cascade-delete the other HUBs and their associated ODN features.

## Behavior

1. Keep only currently selected features in `HUB BOX`; delete all other HUB BOX features.
2. In `HUB BOX Boundary`, `SUB BOX`, `SUB BOX Boundary`, and `Distribution Cable`, delete features whose `Name` contains any deleted HUB BOX `Name`.
3. Use the deleted `SUB BOX Boundary` polygons to delete intersecting point features in `Sdus`, `Mdus`, `Church`, `Commercial`, `Hospitals`, `Institution`, and `Offices`.

Missing optional target layers are skipped. The four Name-based target layers must contain a `Name` field.

## Safety and performance

- Uses timer-driven batches on the QGIS main thread to keep the interface responsive during scans.
- Shows a progress dialog and asks for confirmation before changing data.
- If a layer was already in edit mode, changes are added to its existing edit buffer and are not committed.
- If a layer was not in edit mode, the plugin starts editing and commits the changes.
- Back up the project/data before running. Provider-level commits are not atomic across multiple layers.

## Install

Download the repository ZIP, extract the plugin folder, and copy the folder containing `__init__.py` and `metadata.txt` into the QGIS Python plugins directory. Enable **DELETE HUB BOX** in Plugin Manager.
