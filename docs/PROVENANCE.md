# Local implementation provenance

- `raster.py` is extracted from Digital Twin Generator's `sim/render.py`.
  It adds empty-scene handling and near-plane clipping for arbitrary CAD scenes.
- `theme.py` is a bundled snapshot of the existing robo_prober shared theme,
  with its stale orange-colour comment corrected. There is no runtime dependency
  on either repository and those source repositories were not modified.
- Surface raycasting, portable projects, CAD loading, tag editing, GUI and
  exports are implemented in this application.
- ArUco code generation and image detection use OpenCV's public dictionary APIs:
  https://docs.opencv.org/4.8.0/d5/d0b/classcv_1_1aruco_1_1Dictionary.html
- CAD loading uses trimesh; STEP tessellation uses cascadio/OpenCASCADE:
  https://trimesh.org/formats.html
- Native file drop uses TkinterDnD2: https://pypi.org/project/tkinterdnd2/

The tiny block-robot demo is generated here. No user CAD or third-party CAD
model is bundled in the distribution. The downloaded cascadio STEP test fixture
is confined to ignored local test artifacts.
