"""Tk desktop studio. Workers render/import; Tk owns every widget and callback."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD

from . import theme
from .core import (
    DICTIONARIES,
    Tag,
    custom_dictionary,
    demo_project,
    dictionary,
    load_model,
    marker_image,
    open_project,
    pick_surface,
    render,
    save_project,
)


class Studio:
    def __init__(self, root, project_path=None, *, session_dir=None):
        self.root = root
        theme.apply(root)
        root.title("ArUco Simulator")
        root.geometry("1440x960")
        root.minsize(1060, 760)
        self.project = open_project(project_path) if project_path else demo_project()
        self.session = Path(
            session_dir
            or Path.home()
            / "aruco_simulator_maps"
            / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        self.session.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="aruco-studio"
        )
        self.future = None
        self.job_kind = None
        self.pending = True
        self.generation = 0
        self.closed = False
        self.result = None
        self.displayed = None
        self.drag = None
        self.orbit = None
        self.placing = False
        self.drag_start = None
        self.selected = None
        self.undo_stack = []
        self.redo_stack = []
        self.playing = False
        self.frame = 0
        self.play_time = time.monotonic()
        self.autosave_due = time.monotonic() + 0.5
        self.save_future = None
        self.fullscreen = False
        self.export_cancel = False
        self.export_progress = 0
        self.import_path = None
        self.status = tk.StringVar(
            value="Drag a tag from the left onto a model surface. Right-drag to orbit; scroll to zoom."
        )
        self.family = tk.StringVar(value=self.project.family)
        self.tag_id = tk.StringVar(value=str(self.project.next_id() or 0))
        self.size = tk.StringVar(value="20")
        self.spin = tk.StringVar(value="0")
        self.lift = tk.StringVar(value="0.3")
        self.units = tk.StringVar(value="mm")
        self.overlay = tk.BooleanVar(value=True)
        self.bits = tk.StringVar(value="4")
        self.count = tk.StringVar(value="50")
        self.seed = tk.StringVar(value="42")
        self.path_var = tk.StringVar()
        self.model_info = tk.StringVar()
        self.metrics = tk.StringVar(value="Rendering simulated camera…")
        self.part_var = tk.StringVar()
        self.axis = tk.StringVar(value="Z")
        self.angle = tk.DoubleVar(value=0)
        self.pivot = [tk.StringVar(value="0") for _ in range(3)]
        self.cam_vars = {
            name: tk.DoubleVar(value=getattr(self.project.camera, name))
            for name in ("yaw", "pitch", "distance", "fov", "blur", "noise", "exposure")
        }
        self.resolution = tk.StringVar(
            value=f"{self.project.camera.width} × {self.project.camera.height}"
        )
        self.speed = tk.DoubleVar(value=20)
        self.model_rotation = 0
        self._build()
        self._refresh_project()
        self._palette()
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())
        self.root.bind("<Control-s>", lambda e: self.save_as())
        self.root.bind("<Control-o>", lambda e: self.browse())
        self.root.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.root.bind("<Escape>", self.escape)
        self.root.bind("<Control-q>", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", self._drop_tag)
        self.root.after(40, self._poll)

    def _build(self):
        header = ttk.Frame(self.root, padding=(18, 12))
        header.pack(fill="x")
        ttk.Label(
            header, text="ARUCO  /  SIMULATOR", font=("TkDefaultFont", 19, "bold")
        ).pack(side="left")
        ttk.Label(header, text="  CAD → TAGS → CAMERA", style="Muted.TLabel").pack(
            side="left", padx=18
        )
        for title, fn in [
            ("Exit", self.close),
            ("Full screen", self.toggle_fullscreen),
            ("Minimize", self.root.iconify),
        ]:
            ttk.Button(header, text=title, command=fn).pack(side="right", padx=3)
        bar = ttk.Frame(self.root, padding=(18, 0, 18, 8))
        bar.pack(fill="x")
        for title, fn in [
            ("Open CAD / project", self.browse),
            ("Demo robot", self.demo),
            ("Resume latest", self.resume),
            ("Save project…", self.save_as),
        ]:
            ttk.Button(bar, text=title, command=fn).pack(side="left", padx=(0, 7))
        ttk.Label(bar, text="Mesh units").pack(side="left", padx=(15, 5))
        ttk.Combobox(
            bar,
            textvariable=self.units,
            values=["mm", "cm", "m", "inch"],
            state="readonly",
            width=6,
        ).pack(side="left")
        ttk.Label(
            bar, text="STEP / GLB / 3MF use file units", style="Muted.TLabel"
        ).pack(side="left", padx=10)
        ttk.Label(
            self.root,
            textvariable=self.model_info,
            style="Muted.TLabel",
            padding=(18, 0, 18, 8),
        ).pack(fill="x")
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=18)
        controls = ttk.Frame(body, width=355)
        body.add(controls, weight=0)
        self.tabs = ttk.Notebook(controls)
        self.tabs.pack(fill="both", expand=True)
        self.scrollers = []
        self.tag_tab = self.scroll_tab("1  Place tags")
        self.camera_tab = self.scroll_tab("2  Camera")
        self.export_tab = self.scroll_tab("3  Export")
        self.root.bind_all("<MouseWheel>", self._scroll_controls, add="+")
        self.root.bind_all("<Button-4>", self._scroll_controls, add="+")
        self.root.bind_all("<Button-5>", self._scroll_controls, add="+")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.changed(False))
        self._tag_controls()
        self._camera_controls()
        self._export_controls()
        right = ttk.Frame(body)
        body.add(right, weight=1)
        viewport_bar = ttk.Frame(right, padding=(10, 0, 0, 7))
        viewport_bar.pack(fill="x")
        ttk.Label(viewport_bar, text="SIMULATED VIEW", style="Big.TLabel").pack(
            side="left"
        )
        ttk.Checkbutton(
            viewport_bar,
            text="Detection overlay",
            variable=self.overlay,
            command=self._show_result,
        ).pack(side="left", padx=10)
        ttk.Button(viewport_bar, text="Fit model", command=self.fit).pack(side="right")
        self.canvas = tk.Canvas(
            right, bg="#141a18", highlightthickness=1, highlightbackground=theme.BORDER
        )
        self.canvas.pack(fill="both", expand=True, padx=(10, 0))
        self.canvas.bind("<Configure>", lambda e: self._show_result())
        self.canvas.bind("<ButtonPress-3>", self._orbit_start)
        self.canvas.bind("<B3-Motion>", self._orbit_move)
        self.canvas.bind("<ButtonRelease-3>", lambda e: setattr(self, "orbit", None))
        self.canvas.bind(
            "<MouseWheel>", lambda e: self.zoom(0.9 if e.delta > 0 else 1.1)
        )
        self.canvas.bind("<Button-4>", lambda e: self.zoom(0.9))
        self.canvas.bind("<Button-5>", lambda e: self.zoom(1.1))
        self.canvas.bind("<ButtonPress-1>", self._canvas_press)
        self.canvas.drop_target_register(DND_FILES)
        self.canvas.dnd_bind("<<Drop>>", self.drop_file)
        ttk.Label(
            right, textvariable=self.metrics, padding=(10, 8), style="Big.TLabel"
        ).pack(fill="x")
        self.results = ttk.Treeview(
            right, columns=("id", "part", "state", "pixels"), show="headings", height=5
        )
        for col, title, width in [
            ("id", "ID", 55),
            ("part", "Attached part", 180),
            ("state", "Camera result", 160),
            ("pixels", "Black square / px", 130),
        ]:
            self.results.heading(col, text=title)
            self.results.column(col, width=width)
        self.results.pack(fill="x", padx=(10, 0))
        self.results.bind("<<TreeviewSelect>>", self.select_tag)
        ttk.Label(
            self.root, textvariable=self.status, wraplength=1300, padding=(18, 10)
        ).pack(fill="x")

    def scroll_tab(self, title):
        wrapper = ttk.Frame(self.tabs)
        self.tabs.add(wrapper, text=title)
        canvas = tk.Canvas(wrapper, bg=theme.BG, highlightthickness=0, width=340)
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas, padding=12)
        window = canvas.create_window(0, 0, window=content, anchor="nw")
        content.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(window, width=e.width)
        )
        self.scrollers.append((canvas, content))
        return content

    def _scroll_controls(self, event):
        for canvas, content in self.scrollers:
            if str(event.widget).startswith(str(content)) or event.widget == canvas:
                bounds = canvas.bbox("all")
                if bounds and bounds[3] > canvas.winfo_height():
                    delta = (
                        -1
                        if getattr(event, "num", None) == 4
                        or getattr(event, "delta", 0) > 0
                        else 1
                    )
                    canvas.yview_scroll(delta * 3, "units")
                break

    def note(self, parent, text):
        ttk.Label(
            parent, text=text, wraplength=310, justify="left", style="Muted.TLabel"
        ).pack(fill="x", pady=(0, 9))

    def field(self, parent, label, var, values=None, width=12):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label).pack(side="left")
        widget = (
            ttk.Combobox(
                row, textvariable=var, values=values, state="readonly", width=width
            )
            if values is not None
            else ttk.Entry(row, textvariable=var, width=width)
        )
        widget.pack(side="right")
        return widget

    def _tag_controls(self):
        p = self.tag_tab
        self.note(
            p,
            "Drag the tag tile onto a face. Drag a placed tag to move it. Right-drag the view to orbit.",
        )
        box = self.field(p, "Dictionary", self.family, DICTIONARIES + ["CUSTOM"], 20)
        box.bind("<<ComboboxSelected>>", lambda e: self.change_family())
        self.field(p, "Marker ID", self.tag_id)
        self.field(p, "Black square / mm", self.size)
        self.field(p, "Rotation / degrees", self.spin)
        self.field(p, "Surface lift / mm", self.lift)
        self.palette = tk.Label(p, bg=theme.SURFACE, cursor="hand2", pady=8)
        self.palette.pack(fill="x", pady=8)
        self.palette.bind("<ButtonPress-1>", self._start_drag)
        ttk.Button(
            p,
            text="Refresh tile / click to place",
            command=self.prepare_tag,
            style="Accent.TButton",
        ).pack(fill="x")
        row = ttk.Frame(p)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="Update selected", command=self.update_tag).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(row, text="Remove", command=self.remove_tag).pack(
            side="left", padx=(5, 0)
        )
        row = ttk.Frame(p)
        row.pack(fill="x")
        ttk.Button(row, text="Undo", command=self.undo).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(row, text="Redo", command=self.redo).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Separator(p).pack(fill="x", pady=10)
        self.note(
            p,
            "One dictionary per project. Changing it regenerates every tile. Import OpenCV bytesList JSON or generate a code family.",
        )
        row = ttk.Frame(p)
        row.pack(fill="x")
        for label, var, width in [
            ("Bits", self.bits, 3),
            ("Count", self.count, 4),
            ("Seed", self.seed, 5),
        ]:
            ttk.Label(row, text=label).pack(side="left")
            ttk.Entry(row, textvariable=var, width=width).pack(side="left", padx=(3, 6))
        row = ttk.Frame(p)
        row.pack(fill="x", pady=5)
        ttk.Button(row, text="Generate", command=self.generate_dictionary).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(row, text="Import JSON", command=self.import_dictionary).pack(
            side="left", fill="x", expand=True
        )

    def slider(self, parent, label, var, low, high, callback):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text=label).pack(side="left")
        value = ttk.Label(row, width=8, anchor="e")
        value.pack(side="right")

        def update(*_):
            value.config(text=f"{var.get():.2f}")

        var.trace_add("write", update)
        update()
        scale = ttk.Scale(
            parent, variable=var, from_=low, to=high, command=lambda v: callback()
        )
        scale.pack(fill="x")
        return scale

    def _camera_controls(self):
        p = self.camera_tab
        self.note(
            p,
            "Test readability in the rendered image. Blur is in pixels; noise is an 8-bit intensity standard deviation.",
        )
        self.slider(
            p, "Orbit / degrees", self.cam_vars["yaw"], -180, 180, self.camera_changed
        )
        self.slider(
            p,
            "Elevation / degrees",
            self.cam_vars["pitch"],
            -80,
            80,
            self.camera_changed,
        )
        self.distance_scale = self.slider(
            p,
            "Distance / metres",
            self.cam_vars["distance"],
            0.05,
            2,
            self.camera_changed,
        )
        self.slider(
            p,
            "Horizontal field of view",
            self.cam_vars["fov"],
            20,
            100,
            self.camera_changed,
        )
        self.slider(
            p, "Defocus blur / px", self.cam_vars["blur"], 0, 5, self.camera_changed
        )
        self.slider(
            p, "Sensor noise", self.cam_vars["noise"], 0, 45, self.camera_changed
        )
        self.slider(
            p,
            "Exposure multiplier",
            self.cam_vars["exposure"],
            0.1,
            2,
            self.camera_changed,
        )
        r = self.field(
            p,
            "Resolution",
            self.resolution,
            ["640 × 480", "800 × 600", "1280 × 960"],
            14,
        )
        r.bind("<<ComboboxSelected>>", lambda e: self.camera_changed())
        row = ttk.Frame(p)
        row.pack(fill="x", pady=8)
        self.play_btn = ttk.Button(row, text="Play camera orbit", command=self.play)
        self.play_btn.pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Reset effects", command=self.reset_effects).pack(
            side="left", padx=5
        )
        self.note(
            p,
            "Placement view stays clean; camera effects apply in Camera and Export. Playback speed follows render throughput.",
        )
        ttk.Separator(p).pack(fill="x", pady=7)
        part = self.field(p, "Rotate a part", self.part_var, [], 18)
        self.part_combo = part
        part.bind("<<ComboboxSelected>>", lambda e: self.part_selected())
        axis = self.field(p, "Rotation axis", self.axis, ["X", "Y", "Z"], 5)
        axis.bind("<<ComboboxSelected>>", lambda e: self.part_changed())
        self.slider(
            p, "Part rotation / degrees", self.angle, -180, 180, self.part_changed
        )
        row = ttk.Frame(p)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Pivot mm  X/Y/Z").pack(side="left")
        for v in self.pivot:
            ttk.Entry(row, textvariable=v, width=6).pack(side="left", padx=2)
        ttk.Button(p, text="Set pivot", command=self.part_changed).pack(
            fill="x", pady=3
        )

    def _export_controls(self):
        p = self.export_tab
        self.note(
            p,
            "Projects contain the working mesh, tag mounts, dictionary and camera settings. Original CAD files are never changed.",
        )
        ttk.Button(
            p,
            text="Save portable .aruco project",
            command=self.save_as,
            style="Accent.TButton",
        ).pack(fill="x", pady=5)
        ttk.Button(
            p, text="Export camera frame + report", command=self.export_frame
        ).pack(fill="x", pady=5)
        ttk.Button(p, text="Export tag PNGs + manifest", command=self.export_tags).pack(
            fill="x", pady=5
        )
        ttk.Button(p, text="Export 360° camera video", command=self.export_video).pack(
            fill="x", pady=5
        )
        ttk.Button(
            p,
            text="Cancel video export",
            command=lambda: setattr(self, "export_cancel", True),
        ).pack(fill="x", pady=5)
        ttk.Separator(p).pack(fill="x", pady=12)
        self.note(p, "Autosave folder:")
        ttk.Label(p, text=str(self.session), wraplength=310).pack(fill="x")
        self.note(
            p,
            "\nSimulation only. A decoded render is not a guarantee that a physical printer or camera can reproduce it. Flat tiles can bridge curved faces; check mount clearance.",
        )
        self.note(
            p,
            "Part rotations are independent rigid transforms, not a robot joint solver. CAD textures/materials are shown as neutral surfaces. Large meshes use a simplified working copy.",
        )

    def _palette(self):
        try:
            d = dictionary(self.family.get(), self.project.custom)
            img = marker_image(d, int(self.tag_id.get()))
            self._tile = ImageTk.PhotoImage(
                Image.fromarray(img).resize((104, 104), Image.Resampling.NEAREST),
                master=self.root,
            )
            self.palette.configure(
                image=self._tile,
                text=f"ID {self.tag_id.get()}  ·  drag onto model",
                compound="top",
                fg=theme.TEXT,
            )
        except (ValueError, cv2.error) as exc:
            self.status.set(str(exc))

    def _tag_settings(self):
        values = (
            int(self.tag_id.get()),
            float(self.size.get()),
            float(self.spin.get()),
            float(self.lift.get()),
        )
        d = dictionary(self.project.family, self.project.custom)
        if not 0 <= values[0] < len(d.bytesList):
            raise ValueError(f"ID must be 0–{len(d.bytesList) - 1}.")
        if (
            not all(np.isfinite(values))
            or not 0.1 <= values[1] <= 2000
            or not 0 <= values[3] <= 1000
        ):
            raise ValueError(
                "Use a positive size and a non-negative lift (millimetres)."
            )
        return values

    def prepare_tag(self):
        try:
            self._tag_settings()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._palette()
        self.placing = True
        self.status.set(
            "Click a model face to place this tag, or drag the tile. Escape cancels."
        )

    def _start_drag(self, event):
        if self.playing or self.job_kind in ("load", "video"):
            self.status.set("Stop playback or wait for the job before placing tags.")
            return
        try:
            self._tag_settings()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._palette()
        self.drag = ("new", None)
        self.palette.grab_set()
        self.root.configure(cursor="crosshair")

    def _drag_move(self, event):
        if self.drag:
            x = event.x_root - self.canvas.winfo_rootx()
            y = event.y_root - self.canvas.winfo_rooty()
            self.canvas.delete("ghost")
            self.canvas.create_rectangle(
                x - 20,
                y - 20,
                x + 20,
                y + 20,
                outline=theme.ACCENT,
                width=2,
                tags="ghost",
            )

    def _drop_tag(self, event):
        if not self.drag:
            return
        drag = self.drag
        self.drag = None
        self.root.configure(cursor="")
        self.canvas.delete("ghost")
        try:
            current = self.root.grab_current()
            if current:
                current.grab_release()
        except tk.TclError:
            pass
        if (
            drag[0] == "move"
            and self.drag_start
            and np.hypot(
                event.x_root - self.drag_start[0], event.y_root - self.drag_start[1]
            )
            < 4
        ):
            return
        x = event.x_root - self.canvas.winfo_rootx()
        y = event.y_root - self.canvas.winfo_rooty()
        self.place_at(x, y, drag[1] if drag[0] == "move" else None)

    def image_coords(self, x, y):
        if not hasattr(self, "image_rect"):
            return None
        ox, oy, scale = self.image_rect
        u = (x - ox) / scale
        v = (y - oy) / scale
        if (
            self.displayed
            and 0 <= u < self.displayed.camera.width
            and 0 <= v < self.displayed.camera.height
        ):
            return u, v
        return None

    def place_at(self, x, y, move_index=None):
        uv = self.image_coords(x, y)
        if uv is None:
            return
        if self.displayed_generation != self.generation:
            self.status.set("View is updating. Drop again when the render finishes.")
            return
        hit = pick_surface(self.displayed, *uv)
        if hit is None:
            self.status.set("Drop onto a solid model surface, not the background.")
            return
        try:
            tag_id, size, spin, lift = self._tag_settings()
            if move_index is None and any(t.id == tag_id for t in self.project.tags):
                raise ValueError(
                    "This ID is already placed. Choose a unique ID or move the existing tag."
                )
            self.checkpoint()
            if move_index is None:
                self.project.tags.append(Tag(tag_id, *hit, size, spin, lift))
                self.selected = None
                new = self.project.next_id()
                if new is not None:
                    self.tag_id.set(str(new))
                    self._palette()
            else:
                t = self.project.tags[move_index]
                t.part, t.point, t.normal = hit
                self.selected = move_index
            self.placing = False
            self.changed()
            self.status.set(
                f"Tag attached to {self.project.parts[hit[0]].name}. Autosaved locally."
            )
        except ValueError as exc:
            self.status.set(str(exc))

    def _canvas_press(self, event):
        if self.playing:
            return
        if self.placing:
            self.place_at(event.x, event.y)
            return
        uv = self.image_coords(event.x, event.y)
        if uv is None or self.result is None:
            return
        for i, t in enumerate(self.displayed.tags):
            T = (
                self.result["camera_from_world"]
                @ self.displayed.parts[t.part].transform()
                @ t.local_frame()
            )
            p = T[:3, 3]
            if p[2] <= 0:
                continue
            q = self.result["K"] @ p
            center = q[:2] / q[2]
            x, y = np.round(center).astype(int)
            depth = self.result["depth"]
            if not (0 <= x < depth.shape[1] and 0 <= y < depth.shape[0]):
                continue
            if p[2] > depth[y, x] + 0.002 or T[:3, 2] @ (-p) <= 0:
                continue
            if np.linalg.norm(center - uv) < 28:
                self.selected = i
                self._selected_fields()
                self.drag = ("move", i)
                self.drag_start = (event.x_root, event.y_root)
                self.canvas.grab_set()
                return

    def select_tag(self, event=None):
        selection = self.results.selection()
        if not selection:
            return
        self.selected = int(selection[0])
        self._selected_fields()

    def _selected_fields(self):
        if self.selected is None or self.selected >= len(self.project.tags):
            return
        t = self.project.tags[self.selected]
        for var, value in [
            (self.tag_id, t.id),
            (self.size, t.size_mm),
            (self.spin, t.spin),
            (self.lift, t.lift_mm),
        ]:
            var.set(str(value))
        self._palette()
        self.status.set(
            f"Selected ID {t.id}. Update its settings, remove it, or drag it onto another face."
        )

    def update_tag(self):
        if self.selected is None:
            self.status.set("Select a placed tag in the table first.")
            return
        try:
            tag_id, size, spin, lift = self._tag_settings()
            if any(
                i != self.selected and t.id == tag_id
                for i, t in enumerate(self.project.tags)
            ):
                raise ValueError("That ID is already used.")
            self.checkpoint()
            t = self.project.tags[self.selected]
            t.id, t.size_mm, t.spin, t.lift_mm = tag_id, size, spin, lift
            self.changed()
        except ValueError as exc:
            self.status.set(str(exc))

    def remove_tag(self):
        if self.selected is None:
            return
        self.checkpoint()
        self.project.tags.pop(self.selected)
        self.selected = None
        self.changed()
        self.tag_id.set(str(self.project.next_id() or 0))
        self._palette()

    def checkpoint(self):
        self.undo_stack.append(copy.deepcopy(self.project))
        self.undo_stack = self.undo_stack[-20:]
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.project)
        self.project = self.undo_stack.pop()
        self.selected = None
        self._refresh_project()
        self.changed()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.project)
        self.project = self.redo_stack.pop()
        self.selected = None
        self._refresh_project()
        self.changed()

    def change_family(self):
        try:
            d = dictionary(self.family.get(), self.project.custom)
            if any(t.id >= len(d.bytesList) for t in self.project.tags):
                raise ValueError(
                    "Existing IDs exceed this dictionary. Remove or renumber those tags first."
                )
            self.checkpoint()
            self.project.family = self.family.get()
            self.changed()
            self._palette()
        except ValueError as exc:
            self.family.set(self.project.family)
            self.status.set(str(exc))

    def generate_dictionary(self):
        try:
            count, bits, seed = (
                int(self.count.get()),
                int(self.bits.get()),
                int(self.seed.get()),
            )
            if any(t.id >= count for t in self.project.tags):
                raise ValueError("Count must include all placed IDs.")
            if not 1 <= count <= 250 or bits not in range(4, 8):
                raise ValueError("Use 1–250 codes, 4–7 bits.")
            self._job("dictionary", lambda: custom_dictionary(count, bits, seed))
            self.status.set("Generating custom codes…")
        except ValueError as exc:
            self.status.set(str(exc))

    def import_dictionary(self):
        path = filedialog.askopenfilename(filetypes=[("Dictionary JSON", "*.json")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
            d = dictionary("CUSTOM", data)
            if any(t.id >= len(d.bytesList) for t in self.project.tags):
                raise ValueError("Existing IDs exceed this dictionary.")
            self.checkpoint()
            self.project.custom = data
            self.project.family = "CUSTOM"
            self.family.set("CUSTOM")
            self.changed()
            self._palette()
        except Exception as exc:
            self.status.set(f"Dictionary could not be loaded: {exc}")

    def _refresh_project(self):
        p = self.project
        self.family.set(p.family)
        self.tag_id.set(str(p.next_id() or 0))
        self._camera_fields()
        count = sum(len(part.faces) for part in p.parts)
        lo, hi = p.extent()
        dims = (hi - lo) * 1000
        self.model_info.set(
            f"{p.source}   •   {len(p.parts)} parts   •   {count:,} working triangles / {p.source_faces:,} source   •   {dims[0]:.1f} × {dims[1]:.1f} × {dims[2]:.1f} mm"
        )
        self.part_combo.configure(
            values=[f"{i}: {part.name}" for i, part in enumerate(p.parts)]
        )
        self.part_var.set(f"0: {p.parts[0].name}")
        self.part_selected()
        self._palette()

    def _camera_fields(self):
        for name, var in self.cam_vars.items():
            var.set(getattr(self.project.camera, name))
        self.resolution.set(
            f"{self.project.camera.width} × {self.project.camera.height}"
        )
        lo, hi = self.project.extent()
        r = max(np.linalg.norm(hi - lo) / 2, 0.01)
        self.distance_scale.configure(
            from_=r * 1.1, to=max(r * 15, self.project.camera.distance * 2)
        )

    def camera_changed(self):
        for name, var in self.cam_vars.items():
            setattr(self.project.camera, name, float(var.get()))
        self.project.camera.width, self.project.camera.height = [
            int(x.strip()) for x in self.resolution.get().split("×")
        ]
        self.changed()

    def part_selected(self):
        try:
            p = self.project.parts[int(self.part_var.get().split(":")[0])]
            self.axis.set(p.axis)
            self.angle.set(p.angle)
            for var, value in zip(self.pivot, p.pivot):
                var.set(f"{value * 1000:.2f}")
        except (ValueError, IndexError):
            return

    def part_changed(self):
        try:
            p = self.project.parts[int(self.part_var.get().split(":")[0])]
            pivot = [float(v.get()) * 0.001 for v in self.pivot]
            if not np.isfinite(pivot).all():
                raise ValueError("Pivot must be finite.")
            p.axis = self.axis.get()
            p.angle = self.angle.get()
            p.pivot = pivot
            self.changed()
        except ValueError as exc:
            self.status.set(str(exc))

    def _orbit_start(self, event):
        self.orbit = (
            event.x,
            event.y,
            self.project.camera.yaw,
            self.project.camera.pitch,
        )

    def _orbit_move(self, event):
        if not self.orbit:
            return
        x, y, yaw, pitch = self.orbit
        self.project.camera.yaw = ((yaw - (event.x - x) * 0.35 + 180) % 360) - 180
        self.project.camera.pitch = float(
            np.clip(pitch + (event.y - y) * 0.25, -80, 80)
        )
        self._camera_fields()
        self.changed()

    def zoom(self, factor):
        lo, hi = self.project.extent()
        r = max(np.linalg.norm(hi - lo) / 2, 0.01)
        self.project.camera.distance = float(
            np.clip(self.project.camera.distance * factor, r * 1.1, r * 30)
        )
        self._camera_fields()
        self.changed()

    def fit(self):
        self.project.fit_camera()
        self._camera_fields()
        self.changed()

    def reset_effects(self):
        self.project.camera.blur = 0
        self.project.camera.noise = 0
        self.project.camera.exposure = 1
        self._camera_fields()
        self.changed()

    def play(self):
        self.playing = not self.playing
        self.play_time = time.monotonic()
        self.play_btn.configure(
            text="Pause orbit" if self.playing else "Play camera orbit"
        )
        self.pending = True

    def escape(self, event=None):
        self.placing = False
        self.drag = None
        self.canvas.delete("ghost")
        self.root.configure(cursor="")
        if self.playing:
            self.play()
        self.fullscreen = False
        self.root.attributes("-fullscreen", False)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def browse(self):
        path = filedialog.askopenfilename(
            title="Open CAD or saved project",
            filetypes=[
                (
                    "CAD / meshes / projects",
                    "*.aruco *.stl *.obj *.ply *.glb *.gltf *.3mf *.step *.stp",
                ),
                ("All files", "*"),
            ],
        )
        if path:
            self.load(path)

    def drop_file(self, event):
        paths = self.root.tk.splitlist(event.data)
        if paths:
            self.load(paths[0])
        return "copy"

    def load(self, path):
        if self.playing:
            self.play()
        self.import_path = path
        units = self.units.get()
        if self._job(
            "load",
            lambda: (
                open_project(path)
                if Path(path).suffix.lower() == ".aruco"
                else load_model(path, units)
            ),
        ):
            self.status.set(f"Opening {Path(path).name}…")

    def demo(self):
        if self.playing:
            self.play()
        self.checkpoint()
        self.project = demo_project()
        self.selected = None
        self._refresh_project()
        self.changed()

    def resume(self):
        files = sorted(
            (Path.home() / "aruco_simulator_maps").glob("*/project.aruco"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        files = [p for p in files if p.parent != self.session]
        if files:
            self.load(files[0])
        else:
            self.status.set("No earlier autosaved project found.")

    def changed(self, save=True):
        self.generation += 1
        self.pending = True
        if save:
            self.autosave_due = time.monotonic() + 0.7

    def _job(self, kind, fn):
        if self.future is not None and self.job_kind != "render":
            self.status.set("Wait for the current job to finish.")
            return False
        # The single worker serializes jobs; stale render output is discarded.
        if self.future is not None:
            self.future.cancel()
        self.job_kind = kind
        self.future = self.executor.submit(fn)
        return True

    def _poll(self):
        if self.closed:
            return
        try:
            if self.job_kind == "video":
                self.status.set(
                    f"Exporting orbit: {self.export_progress} / 120 frames. Cancel keeps the partial video."
                )
            if self.future is not None and self.future.done():
                future = self.future
                kind = self.job_kind
                self.future = None
                self.job_kind = None
                try:
                    value = future.result()
                    if kind == "render":
                        generation, project, result, elapsed = value
                        if generation == self.generation:
                            self.displayed_generation = generation
                            self.displayed = project
                            self.result = result
                            self._show_result()
                            self.metrics.set(
                                f"{result['detected']} / {len(project.tags)} tags detected   •   {project.camera.width} × {project.camera.height}   •   {elapsed:.2f} s / render"
                            )
                            self.results.delete(*self.results.get_children())
                            for i, row in enumerate(result["rows"]):
                                self.results.insert(
                                    "",
                                    "end",
                                    iid=str(i),
                                    values=(
                                        row["id"],
                                        row["part"],
                                        row["status"],
                                        f"{row['pixels']:.1f}"
                                        if "pixels" in row
                                        else "—",
                                    ),
                                )
                    elif kind == "load":
                        self.checkpoint()
                        self.project = value
                        self.selected = None
                        self._refresh_project()
                        self.changed()
                        self.status.set(
                            "Loaded. Model dimensions are shown above; set mesh units before importing if the size is wrong."
                        )
                    elif kind == "dictionary":
                        self.checkpoint()
                        self.project.custom = value
                        self.project.family = "CUSTOM"
                        self.family.set("CUSTOM")
                        self.changed()
                        self._palette()
                        self.status.set(
                            "Custom dictionary ready; it is embedded in the project and tag export."
                        )
                    elif kind == "video":
                        self.status.set(value)
                except Exception as exc:
                    self.status.set(f"Could not complete {kind}: {exc}")
            if self.save_future is not None and self.save_future.done():
                try:
                    self.save_future.result()
                except Exception as exc:
                    self.status.set(f"Autosave failed: {exc}")
                self.save_future = None
            if self.future is None and self.playing:
                self.project.camera.yaw = (
                    (self.project.camera.yaw + 3 + 180) % 360
                ) - 180
                self._camera_fields()
                self.frame += 1
                self.changed()
            if self.future is None and self.pending:
                project = copy.deepcopy(self.project)
                generation = self.generation
                effects = self.tabs.index("current") != 0
                frame = self.frame

                def work():
                    start = time.monotonic()
                    result = render(project, effects, frame)
                    return generation, project, result, time.monotonic() - start

                self.pending = False
                self._job("render", work)
            if (
                self.autosave_due
                and time.monotonic() >= self.autosave_due
                and self.save_future is None
                and self.job_kind != "video"
            ):
                project = copy.deepcopy(self.project)
                self.save_future = self.executor.submit(
                    save_project, project, self.session / "project.aruco"
                )
                self.autosave_due = None
        finally:
            if not self.closed:
                self.root.after(40, self._poll)

    def _show_result(self):
        if self.result is None:
            return
        pixels = self.result["overlay" if self.overlay.get() else "image"]
        image = Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB))
        w = max(self.canvas.winfo_width(), 1)
        h = max(self.canvas.winfo_height(), 1)
        scale = min(w / image.width, h / image.height)
        size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
        self._view = ImageTk.PhotoImage(image, master=self.root)
        self.canvas.delete("view")
        self.canvas.create_image(w / 2, h / 2, image=self._view, tags="view")
        self.canvas.tag_lower("view")
        self.image_rect = ((w - size[0]) / 2, (h - size[1]) / 2, scale)

    def save_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".aruco",
            initialfile="tagged-model.aruco",
            filetypes=[("ArUco project", "*.aruco")],
        )
        if not path:
            return
        try:
            save_project(self.project, path)
            self.status.set(f"Saved {path}")
        except Exception as exc:
            self.status.set(f"Save failed: {exc}")

    def export_dir(self, label):
        path = self.session / "exports" / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{label}"
        path.mkdir(parents=True)
        return path

    def export_frame(self):
        if self.result is None or self.displayed_generation != self.generation:
            self.status.set("Wait for the current view to finish rendering.")
            return
        folder = self.export_dir("frame")
        cv2.imwrite(str(folder / "camera.png"), self.result["image"])
        cv2.imwrite(str(folder / "detected.png"), self.result["overlay"])
        report = {
            "source": "simulation",
            "verified": False,
            "camera": asdict(self.displayed.camera),
            "dictionary": self.displayed.family,
            "custom": self.displayed.custom,
            "detections": self.result["rows"],
            "camera_matrix": self.result["K"].tolist(),
            "camera_from_world": self.result["camera_from_world"].tolist(),
        }
        (folder / "detections.json").write_text(json.dumps(report, indent=2))
        self.status.set(f"Frame and detections saved: {folder}")

    def export_tags(self):
        try:
            d = dictionary(self.project.family, self.project.custom)
            folder = self.export_dir("tags")
            records = []
            for t in self.project.tags:
                filename = f"tag-{t.id:04d}.png"
                cv2.imwrite(str(folder / filename), marker_image(d, t.id, 80))
                records.append(
                    {
                        **asdict(t),
                        "file": filename,
                        "quiet_zone_cells": 1,
                        "part_name": self.project.parts[t.part].name,
                    }
                )
            (folder / "markers.json").write_text(
                json.dumps(
                    {
                        "dictionary": self.project.family,
                        "custom": self.project.custom,
                        "units": "metres for points; mm for size",
                        "tags": records,
                    },
                    indent=2,
                )
            )
            self.status.set(f"Tag PNGs and size/placement manifest saved: {folder}")
        except Exception as exc:
            self.status.set(f"Tag export failed: {exc}")

    def export_video(self):
        if self.playing:
            self.play()
        project = copy.deepcopy(self.project)
        folder = self.export_dir("orbit")
        self.export_cancel = False
        self.export_progress = 0

        def work():
            output = folder / "orbit.avi"
            writer = cv2.VideoWriter(
                str(output),
                cv2.VideoWriter_fourcc(*"MJPG"),
                24,
                (project.camera.width, project.camera.height),
            )
            if not writer.isOpened():
                raise RuntimeError("MJPEG video writer is unavailable.")
            rows = []
            try:
                for frame in range(120):
                    if self.export_cancel:
                        break
                    project.camera.yaw = (project.camera.yaw + 3 + 180) % 360 - 180
                    result = render(project, True, frame)
                    writer.write(result["overlay"])
                    self.export_progress = frame + 1
                    rows.append(
                        {
                            "frame": frame,
                            "yaw": project.camera.yaw,
                            "tags": result["rows"],
                        }
                    )
            finally:
                writer.release()
            (folder / "detections.json").write_text(
                json.dumps(
                    {"source": "simulation", "verified": False, "frames": rows},
                    indent=2,
                )
            )
            return f"{'Partial orbit' if len(rows) < 120 else '360° orbit'} saved ({len(rows)} frames): {output}"

        if self._job("video", work):
            self.status.set(
                "Exporting 120 orbit frames at 24 fps. You can cancel and keep the partial recording."
            )

    def close(self):
        if self.closed:
            return
        if self.job_kind in ("video", "load", "dictionary"):
            if not messagebox.askyesno(
                "Job running",
                "Stop the current job and close? Your project will be saved.",
            ):
                return
        self.export_cancel = True
        self.executor.shutdown(wait=True, cancel_futures=True)
        try:
            save_project(self.project, self.session / "project.aruco")
        except Exception as exc:
            self.status.set(f"Could not save before closing: {exc}")
            return
        self.closed = True
        self.root.destroy()


def run(project=None):
    root = TkinterDnD.Tk()
    Studio(root, project)
    root.mainloop()
