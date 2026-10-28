import argparse
import importlib.util
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="ArUco Simulator — local CAD camera laboratory"
    )
    parser.add_argument(
        "command", nargs="?", choices=["gui", "doctor", "demo"], default="gui"
    )
    parser.add_argument("--project", help="Open a saved .aruco project")
    parser.add_argument(
        "--out", default="demo.aruco", help="Output for the demo command"
    )
    args = parser.parse_args()
    if args.command == "doctor":
        from . import __version__

        print(f"ArUco Simulator {__version__} — simulation only, no hardware access")
        for name in (
            "numpy",
            "cv2",
            "trimesh",
            "PIL",
            "tkinter",
            "tkinterdnd2",
            "fast_simplification",
            "cascadio",
        ):
            print(f"{'ok' if importlib.util.find_spec(name) else 'MISSING':8s} {name}")
        return
    if args.command == "demo":
        from .core import demo_project, save_project

        path = Path(args.out)
        if path.exists():
            parser.error("Output already exists; choose another --out path.")
        save_project(demo_project(), path)
        print(path.resolve())
        return
    from .gui import run

    run(args.project)


if __name__ == "__main__":
    main()
