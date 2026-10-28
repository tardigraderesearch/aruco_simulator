# Validation — 2026-10-27

Local desktop release 0.1.0. No remote and no publication.

## Executed checks

- 12 pytest tests passed, including GUI tests on the Linux display (no skips).
- The actual OpenCV detector decoded all three demo tags. Strong blur reduced
  the detection count; a foreground mesh hid the tags; a 180-degree part
  rotation carried its tag to the rear and stopped detection.
- Tk mouse events dragged a new tile from its palette onto a mesh surface.
  Editing, undo, redo, camera effects, frame export, tag export and autosave
  reopening passed. The registered file-drop callback correctly parsed a
  path with spaces. Cross-application OS drag-and-drop was not automated.
- The exported 360-degree MJPEG AVI decoded all 120 frames at 800×600.
  Its per-frame report included both visible and fully occluded tag views.
- Custom dictionary projects round-tripped their geometry, IDs, part pose,
  camera settings and dictionary bytes; the rendered image was unchanged.
- STL units, GLB scene transforms, 3MF units, duplicate IDs, malformed
  dictionaries, fully offscreen geometry and near-plane clipping were tested.
- Imported/rendered the user's existing Dummy 13 chest armor 3MF, read-only:
  2,338 source triangles, approximately 19.12 × 18.58 × 20.00 mm.
  Import ~0.012 s, render ~0.125 s on this machine. That asset is not bundled.
- Imported/rendered cascadio's `colored.step` reference fixture locally:
  3,074 triangles, approximately 7.60 × 7.59 × 16.20 mm. Fixture not bundled.
- Launcher doctor ran twice with “All dependencies present. Good to go.”
  Module launch from repository root worked. A fresh private environment was
  created and dependencies installed successfully during this build.
- The three GUI tabs were captured and visually inspected. Controls scroll
  on shorter windows; the screenshots show the actual application.

## Scope of those results

These are software and Linux desktop results, not physical camera or printer
verification. No physical board or camera was opened. Windows/macOS execution,
printed-tag readability and agreement with real camera optics remain pending.
There is no accuracy claim for reconstructing joints or estimating camera pose;
this application tests rendered marker detection and placement.

## Dependency/runtime snapshot

Python 3.14.7; OpenCV 5.0.0; trimesh 5.2.0; NumPy 2.5.4;
TkinterDnD2 0.7.0; cascadio 0.1.1; fast-simplification 0.2.0.
The package specifies compatible ranges rather than a machine-specific lock.
