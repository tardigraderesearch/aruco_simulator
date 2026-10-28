import os
import time
import pytest
from aruco_simulator.core import open_project


def pump(root, app, seconds=0.2, until_render=False):
    end = time.monotonic() + (20 if until_render else seconds)
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)
        if (
            until_render
            and app.result is not None
            and app.displayed_generation == app.generation
        ):
            return
    if until_render:
        raise TimeoutError("GUI did not render")


def test_drag_onto_surface_update_undo_save_and_file_drop(tmp_path):
    if not os.environ.get("DISPLAY"):
        pytest.skip("requires a display")
    from tkinterdnd2 import TkinterDnD
    from aruco_simulator.gui import Studio

    root = TkinterDnD.Tk()
    app = Studio(root, session_dir=tmp_path / "session")
    try:
        pump(root, app, until_render=True)
        assert app.result["detected"] == 3
        # Generate actual drag events from the palette onto the front of the base.
        app.tag_id.set("9")
        app.size.set("10")
        K, cw = app.project.camera.matrices()
        p = cw @ [-0.04, -0.06, 0.0125, 1]
        q = K @ p[:3]
        u, v = q[:2] / q[2]
        ox, oy, scale = app.image_rect
        x = int(ox + u * scale)
        y = int(oy + v * scale)
        rx = app.canvas.winfo_rootx() + x
        ry = app.canvas.winfo_rooty() + y
        app.palette.event_generate("<ButtonPress-1>", x=30, y=30)
        app.palette.event_generate("<B1-Motion>", x=30, y=30, rootx=rx, rooty=ry)
        app.palette.event_generate("<ButtonRelease-1>", x=30, y=30, rootx=rx, rooty=ry)
        pump(root, app, until_render=True)
        assert len(app.project.tags) == 4 and app.project.tags[-1].id == 9
        app.selected = 3
        app._selected_fields()
        app.size.set("12")
        app.update_tag()
        pump(root, app, until_render=True)
        assert app.project.tags[-1].size_mm == 12
        app.undo()
        pump(root, app, until_render=True)
        assert app.project.tags[-1].size_mm == 10
        app.redo()
        pump(root, app, until_render=True)
        assert app.project.tags[-1].size_mm == 12
        app.tabs.select(1)
        pump(root, app)
        app.cam_vars["blur"].set(5)
        app.camera_changed()
        pump(root, app, until_render=True)
        assert app.result["detected"] < 4
        app.export_frame()
        assert list((tmp_path / "session/exports").glob("*/camera.png"))
        app.export_tags()
        assert list((tmp_path / "session/exports").glob("*/markers.json"))
        # Exercise the registered file-drop handler with a native Tcl path list.
        import trimesh

        path = tmp_path / "my cube.stl"
        trimesh.creation.box([50, 50, 50]).export(path)
        before = app.project.source
        from types import SimpleNamespace

        assert app.canvas.dnd_bind("<<Drop>>")
        app.drop_file(SimpleNamespace(data="{" + str(path) + "}"))
        end = time.monotonic() + 20
        while app.project.source == before and time.monotonic() < end:
            pump(root, app)
        pump(root, app, until_render=True)
        assert app.project.source == "my cube.stl"
    finally:
        app.close()
    saved = open_project(tmp_path / "session/project.aruco")
    assert saved.source == "my cube.stl"


def test_orbit_export_is_decodable_and_keeps_camera_evidence(tmp_path):
    if not os.environ.get("DISPLAY"):
        pytest.skip("requires a display")
    from tkinterdnd2 import TkinterDnD
    from aruco_simulator.gui import Studio
    import cv2, json

    root = TkinterDnD.Tk()
    app = Studio(root, session_dir=tmp_path / "orbit-session")
    try:
        pump(root, app, until_render=True)
        app.export_video()
        end = time.monotonic() + 45
        while app.job_kind == "video" and time.monotonic() < end:
            pump(root, app)
        assert app.job_kind != "video", app.status.get()
        files = list((app.session / "exports").glob("*/orbit.avi"))
        assert len(files) == 1
        cap = cv2.VideoCapture(str(files[0]))
        n = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            assert frame.shape == (600, 800, 3)
            n += 1
        cap.release()
        assert n == 120
        data = json.loads(files[0].with_name("detections.json").read_text())
        assert len(data["frames"]) == 120 and not data["verified"]
        counts = [
            sum(t["status"] == "Detected" for t in row["tags"])
            for row in data["frames"]
        ]
        assert max(counts) == 3 and min(counts) == 0
    finally:
        app.close()
