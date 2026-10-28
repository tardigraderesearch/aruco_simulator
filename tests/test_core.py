import cv2
import numpy as np
import pytest
import trimesh
from aruco_simulator.core import (
    demo_project,
    render,
    pick_surface,
    dictionary,
    custom_dictionary,
    marker_image,
    save_project,
    open_project,
    load_model,
    Tag,
)


def test_real_detector_reads_demo_and_fails_under_blur():
    p = demo_project()
    r = render(p)
    assert {row["id"] for row in r["rows"] if row["status"] == "Detected"} == {0, 1, 2}
    p.camera.blur = 5
    assert render(p)["detected"] < 3


def test_surface_pick_and_attached_tag_follow_part():
    p = demo_project()
    p.tags = []
    K, cw = p.camera.matrices()
    point = np.array([0, -0.0275, 0.07])
    q = K @ (cw @ np.r_[point, 1])[:3]
    uv = q[:2] / q[2]
    idx, contact, normal = pick_surface(p, *uv)
    assert idx == 1
    assert np.allclose(contact, point, atol=1e-6)
    p.tags = [Tag(17, idx, contact, normal, 22)]
    assert render(p)["detected"] == 1
    p.parts[idx].angle = 180
    assert render(p)["detected"] == 0  # the tag follows its parent to the rear face


def test_foreground_occludes_tag():
    p = demo_project()
    r = render(p)
    assert r["detected"] == 3
    # Place a broad wall between the camera and the robot.
    from aruco_simulator.core import Part

    m = trimesh.creation.box([1, 0.015, 1])
    m.apply_translation([0, -0.12, 0.2])
    p.parts.append(Part("wall", m.vertices, m.faces))
    assert render(p)["detected"] == 0


def test_portable_project_roundtrip_includes_custom_dictionary(tmp_path):
    p = demo_project()
    p.family = "CUSTOM"
    p.custom = custom_dictionary(25, 4, 42)
    p.parts[2].angle = 15
    p.camera.noise = 3
    path = tmp_path / "project.aruco"
    save_project(p, path)
    restored = open_project(path)
    assert np.array_equal(p.parts[1].vertices, restored.parts[1].vertices)
    assert restored.tags[2].id == 2 and restored.parts[2].angle == 15
    assert np.array_equal(render(p)["image"], render(restored)["image"])
    assert len(dictionary("CUSTOM", restored.custom).bytesList) == 25


def test_mesh_units_and_scene_transforms(tmp_path):
    m = trimesh.creation.box([100, 50, 25])
    path = tmp_path / "space in name.stl"
    m.export(path)
    p = load_model(path, "mm")
    lo, hi = p.extent()
    assert np.allclose(hi - lo, [0.1, 0.05, 0.025])
    scene = trimesh.Scene()
    m = trimesh.creation.box([0.1, 0.05, 0.025])
    T = np.eye(4)
    T[0, 3] = 0.2
    scene.add_geometry(m, node_name="one")
    scene.add_geometry(m, node_name="two", transform=T)
    path = tmp_path / "assembly.glb"
    scene.export(path)
    p = load_model(path, "mm")
    lo, hi = p.extent()
    assert len(p.parts) == 2
    assert np.allclose(hi - lo, [0.3, 0.05, 0.025], atol=1e-6)


def test_tag_png_has_quiet_zone_and_decodes():
    d = dictionary()
    image = marker_image(d, 107)
    _, ids, _ = cv2.aruco.ArucoDetector(d).detectMarkers(image)
    assert ids.flatten().tolist() == [107]
    assert np.all(image[0] == 255)


def test_duplicate_ids_and_bad_dictionary_rejected():
    p = demo_project()
    p.tags[1].id = 0
    with pytest.raises(ValueError, match="own ID"):
        p.validate()
    with pytest.raises(ValueError):
        dictionary("CUSTOM", {"markerSize": 4, "bytesList": [[999]]})


def test_offscreen_model_returns_empty_detection():
    p = demo_project()
    p.camera.target = [100, 100, 100]
    assert render(p)["detected"] == 0


def test_3mf_file_units_override_mesh_selector(tmp_path):
    m = trimesh.creation.box([20, 30, 40])
    m.units = "millimeters"
    path = tmp_path / "model.3mf"
    trimesh.Scene(m).export(path)
    p = load_model(path, "m")
    lo, hi = p.extent()
    assert np.allclose(hi - lo, [0.02, 0.03, 0.04])


def test_near_plane_does_not_drop_whole_mesh():
    from aruco_simulator.raster import Item, Scene, Renderer

    vertices = np.array([[-0.1, -0.1, 0.2], [0, 0.1, -0.1], [0.1, -0.1, 0.2]])
    item = Item("part", vertices, np.array([[0, 1, 2]]), np.ones((1, 3)))
    K = np.array([[100, 0, 100], [0, 100, 100], [0, 0, 1.0]])
    r = Renderer(Scene([item]), K, (200, 200), np.eye(4), supersample=1)
    color, depth = r.render({"part": np.eye(4)})
    assert np.isfinite(depth).sum() > 10000
