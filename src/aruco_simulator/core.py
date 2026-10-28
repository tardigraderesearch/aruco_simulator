"""Portable project model, calibrated pinhole camera and surface-attached markers.

All internal geometry is in metres. Tags are defined by their black square size;
white quiet zones add one bit-cell on each side. Every detection comes from
OpenCV on the rendered pixels, never from the known placement table.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import io
import json
import math
import os
from pathlib import Path
import tempfile
import zipfile

import cv2
import numpy as np
import trimesh

from .raster import Item, Scene, Texture, Renderer, quad

DICTIONARIES = [
    f"DICT_{n}X{n}_{count}" for n in (4, 5, 6, 7) for count in (50, 100, 250, 1000)
]
FORMATS = {".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf", ".step", ".stp"}
UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "inch": 0.0254}
MAX_FACES = 12000


def dictionary(name="DICT_4X4_250", custom=None):
    if name == "CUSTOM":
        if not custom:
            raise ValueError("Load or generate a custom dictionary first.")
        bits = int(custom["markerSize"])
        correction = int(custom.get("maxCorrectionBits", 0))
        raw = np.asarray(custom["bytesList"])
        if (
            bits not in range(3, 8)
            or raw.ndim != 3
            or raw.shape[1:] != (math.ceil(bits * bits / 8), 4)
        ):
            raise ValueError("Invalid OpenCV dictionary dimensions.")
        if (
            not 1 <= len(raw) <= 1000
            or not np.isfinite(raw).all()
            or (raw < 0).any()
            or (raw > 255).any()
        ):
            raise ValueError("Invalid dictionary bytes/count.")
        if (
            not 0 <= correction <= bits * bits // 2
            or not np.equal(raw, np.floor(raw)).all()
        ):
            raise ValueError("Invalid dictionary correction limit or bytes.")
        return cv2.aruco.Dictionary(raw.astype(np.uint8), bits, correction)
    if name not in DICTIONARIES:
        raise ValueError(f"Unsupported dictionary: {name}")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def custom_dictionary(count, bits, seed):
    if not 1 <= count <= 250 or bits not in range(4, 8):
        raise ValueError("Choose 1–250 markers and 4–7 bits.")
    d = cv2.aruco.extendDictionary(count, bits, randomSeed=seed)
    return {
        "markerSize": d.markerSize,
        "maxCorrectionBits": d.maxCorrectionBits,
        "bytesList": d.bytesList.tolist(),
    }


def marker_image(d, tag_id, cell=32):
    if not 0 <= tag_id < len(d.bytesList):
        raise ValueError(f"ID must be between 0 and {len(d.bytesList) - 1}.")
    black = cv2.aruco.generateImageMarker(d, tag_id, (d.markerSize + 2) * cell)
    return cv2.copyMakeBorder(
        black, cell, cell, cell, cell, cv2.BORDER_CONSTANT, value=255
    )


def surface_frame(point, normal, spin=0):
    z = np.asarray(normal, float)
    z = z / np.linalg.norm(z)
    up = [0, 0, 1] if abs(z[2]) < 0.95 else [0, 1, 0]
    x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    angle = np.radians(spin)
    R = np.array(
        [
            [np.cos(angle), -np.sin(angle), 0],
            [np.sin(angle), np.cos(angle), 0],
            [0, 0, 1],
        ]
    )
    T = np.eye(4)
    T[:3, :3] = np.c_[x, y, z] @ R
    T[:3, 3] = point
    return T


@dataclass
class Part:
    name: str
    vertices: np.ndarray
    faces: np.ndarray
    color: tuple = (0.40, 0.49, 0.46)  # BGR
    angle: float = 0.0
    axis: str = "Z"
    pivot: list = field(default_factory=lambda: [0.0, 0.0, 0.0])

    def transform(self):
        v = np.zeros(3)
        v["XYZ".index(self.axis)] = np.radians(self.angle)
        R, _ = cv2.Rodrigues(v)
        T = np.eye(4)
        T[:3, :3] = R
        p = np.asarray(self.pivot)
        T[:3, 3] = p - R @ p
        return T


@dataclass
class Tag:
    id: int
    part: int
    point: list
    normal: list
    size_mm: float = 20.0
    spin: float = 0.0
    lift_mm: float = 0.3

    def local_frame(self):
        return surface_frame(
            np.array(self.point) + np.array(self.normal) * self.lift_mm * 0.001,
            self.normal,
            self.spin,
        )


@dataclass
class Camera:
    yaw: float = -70.0
    pitch: float = 22.0
    distance: float = 0.55
    fov: float = 48.0
    width: int = 800
    height: int = 600
    target: list = field(default_factory=lambda: [0.0, 0.0, 0.1])
    blur: float = 0.0
    noise: float = 0.0
    exposure: float = 1.0
    seed: int = 0

    def matrices(self):
        a, e = np.radians([self.yaw, self.pitch])
        eye = np.asarray(self.target) + self.distance * np.array(
            [np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)]
        )
        z = np.asarray(self.target) - eye
        z /= np.linalg.norm(z)
        x = np.cross(z, [0, 0, 1.0])
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        world_cam = np.eye(4)
        world_cam[:3, :3] = np.c_[x, y, z]
        world_cam[:3, 3] = eye
        f = self.width / (2 * np.tan(np.radians(self.fov) / 2))
        K = np.array(
            [[f, 0, (self.width - 1) / 2], [0, f, (self.height - 1) / 2], [0, 0, 1]]
        )
        return K, np.linalg.inv(world_cam)


@dataclass
class Project:
    parts: list[Part]
    tags: list[Tag] = field(default_factory=list)
    family: str = "DICT_4X4_250"
    custom: dict | None = None
    camera: Camera = field(default_factory=Camera)
    source: str = "Demo robot"
    source_faces: int = 0
    units: str = "mm"

    def extent(self):
        points = np.vstack(
            [
                p.vertices @ p.transform()[:3, :3].T + p.transform()[:3, 3]
                for p in self.parts
            ]
        )
        return points.min(0), points.max(0)

    def fit_camera(self):
        low, high = self.extent()
        self.camera.target = ((low + high) / 2).tolist()
        self.camera.distance = max(float(np.linalg.norm(high - low)) * 1.7, 0.08)

    def next_id(self):
        used = {t.id for t in self.tags}
        return next(
            (
                i
                for i in range(len(dictionary(self.family, self.custom).bytesList))
                if i not in used
            ),
            None,
        )

    def validate(self):
        d = dictionary(self.family, self.custom)
        ids = []
        for p in self.parts:
            if (
                p.axis not in "XYZ"
                or len(p.axis) != 1
                or not np.isfinite([p.angle, *p.pivot]).all()
            ):
                raise ValueError("Invalid part transform.")
        for t in self.tags:
            if not 0 <= t.part < len(self.parts) or not 0 <= t.id < len(d.bytesList):
                raise ValueError("Tag ID or attached part is out of range.")
            if not 0.1 <= t.size_mm <= 2000 or not 0 <= t.lift_mm <= 1000:
                raise ValueError("Tag size/lift is out of range.")
            if (
                len(t.point) != 3
                or len(t.normal) != 3
                or not np.isfinite([*t.point, *t.normal, t.spin]).all()
            ):
                raise ValueError("Invalid tag position.")
            if not np.isclose(np.linalg.norm(t.normal), 1, atol=1e-5):
                raise ValueError("Tag normal must have unit length.")
            ids.append(t.id)
        if len(set(ids)) != len(ids):
            raise ValueError("Each placed tag needs its own ID.")
        c = self.camera
        if (c.width, c.height) not in ((640, 480), (800, 600), (1280, 960)):
            raise ValueError("Unsupported camera resolution.")
        if not np.isfinite(
            [c.yaw, c.pitch, c.distance, c.fov, *c.target, c.blur, c.noise, c.exposure]
        ).all():
            raise ValueError("Camera values must be finite.")
        if not (
            -85 <= c.pitch <= 85
            and 10 <= c.fov <= 120
            and 0.01 <= c.distance <= 10000
            and 0 <= c.blur <= 10
            and 0 <= c.noise <= 100
            and 0.05 <= c.exposure <= 5
        ):
            raise ValueError("Camera settings out of range.")


def demo_project():
    parts = []
    for name, size, pos, color in [
        ("Base", [0.15, 0.12, 0.025], [0, 0, 0.0125], (0.22, 0.27, 0.25)),
        ("Shoulder", [0.055, 0.055, 0.095], [0, 0, 0.0725], (0.38, 0.50, 0.44)),
        ("Upper arm", [0.12, 0.035, 0.035], [0.04, 0, 0.1375], (0.42, 0.61, 0.49)),
        ("Forearm", [0.035, 0.04, 0.10], [0.10, 0, 0.17], (0.38, 0.55, 0.43)),
        ("Gripper", [0.075, 0.045, 0.025], [0.10, 0, 0.2325], (0.28, 0.34, 0.31)),
    ]:
        m = trimesh.creation.box(size)
        m.apply_translation(pos)
        parts.append(Part(name, m.vertices, m.faces, color, pivot=list(pos)))
    p = Project(parts, source_faces=sum(len(x.faces) for x in parts))
    p.tags = [
        Tag(0, 1, [0, -0.0275, 0.07], [0, -1, 0], 22),
        Tag(1, 2, [0.04, -0.0175, 0.1375], [0, -1, 0], 20),
        Tag(2, 3, [0.10, -0.020, 0.18], [0, -1, 0], 20),
    ]
    p.fit_camera()
    return p


def load_model(path, units="mm"):
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in FORMATS:
        raise ValueError(
            "Open STEP, STL, OBJ, PLY, GLB/GLTF or 3MF. Native CAD projects need a STEP or mesh export."
        )
    if path.stat().st_size > 250_000_000:
        raise ValueError("Model exceeds 250 MB. Export a lighter mesh first.")
    scene = trimesh.load_scene(path, process=True)
    # GLTF and STEP conversion carry metre units through GLB; 3MF carries units.
    scale = UNITS[units]
    auto_units = path.suffix.lower() in (".glb", ".gltf", ".step", ".stp", ".3mf")
    if auto_units:
        declared = scene.units or (
            "meters" if path.suffix.lower() != ".3mf" else "millimeters"
        )
        scale = trimesh.units.unit_conversion(declared, "meters")
    nodes = list(scene.graph.nodes_geometry)
    if not nodes or len(nodes) > 256:
        raise ValueError("Model must contain 1–256 mesh parts.")
    source_faces = sum(len(scene.geometry[scene.graph[n][1]].faces) for n in nodes)
    parts = []
    for node in nodes:
        T, key = scene.graph[node]
        m = scene.geometry[key].copy()
        if not isinstance(m, trimesh.Trimesh) or not len(m.faces):
            continue
        m.apply_transform(T)
        m.apply_scale(scale)
        if not np.isfinite(m.vertices).all():
            raise ValueError("Mesh contains invalid coordinates.")
        quota = max(100, int(MAX_FACES * len(m.faces) / max(1, source_faces)))
        if len(m.faces) > quota:
            m = m.simplify_quadric_decimation(face_count=quota)
        parts.append(
            Part(
                str(node),
                np.asarray(m.vertices),
                np.asarray(m.faces),
                pivot=m.bounds.mean(0).tolist(),
            )
        )
    if not parts:
        raise ValueError("No triangle geometry found.")
    all_v = np.vstack([p.vertices for p in parts])
    center = (all_v.min(0) + all_v.max(0)) / 2
    shift = np.array([center[0], center[1], all_v[:, 2].min()])
    for p in parts:
        p.vertices -= shift
        p.pivot = (np.array(p.pivot) - shift).tolist()
    project = Project(
        parts,
        source=path.name,
        source_faces=source_faces,
        units="file" if auto_units else units,
    )
    lo, hi = project.extent()
    if np.linalg.norm(hi - lo) < 1e-7:
        raise ValueError("Mesh has no usable dimensions.")
    project.fit_camera()
    return project


def pick_surface(project, u, v):
    """Vectorized Möller–Trumbore raycast, returning local part-space contact."""
    K, cw = project.camera.matrices()
    wc = np.linalg.inv(cw)
    origin = wc[:3, 3]
    direction = wc[:3, :3] @ (np.linalg.inv(K) @ [u, v, 1.0])
    direction /= np.linalg.norm(direction)
    nearest = None
    best = float("inf")
    for idx, p in enumerate(project.parts):
        T = p.transform()
        inv = np.linalg.inv(T)
        o = inv[:3, :3] @ origin + inv[:3, 3]
        d = inv[:3, :3] @ direction
        a, b, c = p.vertices[p.faces].transpose(1, 0, 2)
        e1 = b - a
        e2 = c - a
        h = np.cross(np.broadcast_to(d, e2.shape), e2)
        det = np.einsum("ij,ij->i", e1, h)
        valid = np.abs(det) > 1e-12
        f = np.divide(1.0, det, out=np.zeros_like(det), where=valid)
        s = o - a
        uu = f * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1)
        vv = f * (q @ d)
        tt = f * np.einsum("ij,ij->i", e2, q)
        mask = valid & (uu >= 0) & (vv >= 0) & (uu + vv <= 1) & (tt > 1e-6)
        if mask.any():
            vals = np.where(mask, tt, np.inf)
            face = int(vals.argmin())
            if vals[face] < best:
                best = float(vals[face])
                normal = np.cross(e1[face], e2[face])
                normal /= np.linalg.norm(normal)
                if normal @ d > 0:
                    normal = -normal
                nearest = (idx, (o + best * d).tolist(), normal.tolist())
    return nearest


def render(project, effects=True, frame=0):
    project.validate()
    d = dictionary(project.family, project.custom)
    items = []
    motions = {}
    for i, p in enumerate(project.parts):
        name = f"part{i}"
        motions[name] = p.transform()
        items.append(
            Item(name, p.vertices, p.faces, np.tile(p.color, (len(p.faces), 1)))
        )
    for t in project.tags:
        T = t.local_frame()
        outer = t.size_mm * 0.001 * (d.markerSize + 4) / (d.markerSize + 2)
        mesh = quad(T, outer, outer)
        texture = Texture(
            marker_image(d, t.id).astype(np.float32) / 255, T, (outer, outer)
        )
        items.append(
            Item(f"part{t.part}", *mesh, np.ones((2, 3)), texture, f"tag{t.id}")
        )
    K, cw = project.camera.matrices()
    cam = project.camera
    # Diffuse light follows the viewing direction; no shadow or lens-distortion claim.
    view = np.linalg.inv(cw)[:3, 3] - np.asarray(cam.target)
    view /= np.linalg.norm(view)
    scene = Scene(items, view, 0.65)
    engine = Renderer(scene, K, (cam.width, cam.height), cw, supersample=1)
    radiance, depth = engine.render(motions)
    background = ~np.isfinite(depth)
    radiance[background] = [0.075, 0.085, 0.082]
    image = np.clip(radiance * 255, 0, 255).astype(np.uint8)
    if effects:
        image = np.clip(image.astype(float) * cam.exposure, 0, 255).astype(np.uint8)
        if cam.blur > 0.01:
            image = cv2.GaussianBlur(image, (0, 0), cam.blur)
        if cam.noise > 0.01:
            noise = np.random.default_rng(cam.seed + frame).normal(
                0, cam.noise, image.shape
            )
            image = np.clip(image.astype(float) + noise, 0, 255).astype(np.uint8)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(d, params)
    corners, ids, _ = detector.detectMarkers(image)
    overlay = image.copy()
    found = {}
    if ids is not None:
        cv2.aruco.drawDetectedMarkers(overlay, corners, ids, borderColor=(122, 230, 46))
        for corner, tag_id in zip(corners, ids.flatten()):
            pts = corner.reshape(4, 2)
            found[int(tag_id)] = {
                "corners": pts.tolist(),
                "pixels": float(
                    np.linalg.norm(pts - np.roll(pts, 1, axis=0), axis=1).mean()
                ),
            }
    rows = []
    for t in project.tags:
        T = cw @ project.parts[t.part].transform() @ t.local_frame()
        point = T[:3, 3]
        facing = float(T[:3, 2] @ (-point)) > 0
        status = (
            "Detected"
            if t.id in found
            else ("Facing away" if not facing else "Not detected")
        )
        if point[2] <= 0:
            status = "Behind camera"
        rows.append(
            {
                "id": t.id,
                "part": project.parts[t.part].name,
                "status": status,
                **found.get(t.id, {}),
            }
        )
    return {
        "image": image,
        "overlay": overlay,
        "depth": depth,
        "rows": rows,
        "detected": len(found),
        "K": K,
        "camera_from_world": cw,
    }


def save_project(project, path):
    project.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema": "aruco-simulator/1",
        "family": project.family,
        "custom": project.custom,
        "source": project.source,
        "source_faces": project.source_faces,
        "units": project.units,
        "camera": asdict(project.camera),
        "tags": [asdict(t) for t in project.tags],
        "parts": [],
    }
    arrays = {}
    for i, p in enumerate(project.parts):
        meta["parts"].append(
            {
                "name": p.name,
                "color": p.color,
                "angle": p.angle,
                "axis": p.axis,
                "pivot": p.pivot,
            }
        )
        arrays[f"v{i}"] = p.vertices
        arrays[f"f{i}"] = p.faces
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    fd, tmp = tempfile.mkstemp(prefix=".aruco-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("scene.json", json.dumps(meta))
            z.writestr("meshes.npz", buf.getvalue())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def open_project(path):
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 250_000_000:
            raise ValueError("Project exceeds 250 MB.")
        meta = json.loads(z.read("scene.json"))
        blob = z.read("meshes.npz")
    if meta.get("schema") != "aruco-simulator/1":
        raise ValueError("Unsupported project version.")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if sum(i.file_size for i in z.infolist()) > 250_000_000:
            raise ValueError("Mesh data exceeds 250 MB.")
    parts = []
    with np.load(io.BytesIO(blob), allow_pickle=False) as arrays:
        for i, info in enumerate(meta["parts"]):
            v = arrays[f"v{i}"]
            f = arrays[f"f{i}"]
            if (
                v.ndim != 2
                or v.shape[1] != 3
                or not np.isfinite(v).all()
                or f.ndim != 2
                or f.shape[1] != 3
                or f.dtype.kind not in "iu"
                or f.min() < 0
                or f.max() >= len(v)
            ):
                raise ValueError("Invalid mesh in project.")
            parts.append(Part(vertices=v, faces=f, **info))
    if not parts or len(parts) > 256:
        raise ValueError("Invalid part count.")
    project = Project(
        parts,
        [Tag(**t) for t in meta["tags"]],
        meta["family"],
        meta["custom"],
        Camera(**meta["camera"]),
        meta["source"],
        meta["source_faces"],
        meta["units"],
    )
    project.validate()
    return project
