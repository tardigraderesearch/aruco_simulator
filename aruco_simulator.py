"""One-command local launcher. Only Python's standard library is used here."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Running pytest or importing from the repo must resolve the package, not this launcher.
if __name__ == "aruco_simulator":
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        __name__,
        HERE / "src/aruco_simulator/__init__.py",
        submodule_search_locations=[str(HERE / "src/aruco_simulator")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
else:
    import hashlib
    import os
    import subprocess

    APP = "aruco_simulator"
    ENV = HERE / ".venv"
    PY = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def say(text):
        print(f"[{APP}] {text}", file=sys.stderr, flush=True)

    def ask(text, yes):
        if yes or not sys.stdin.isatty():
            say(text + " (auto-yes)")
            return
        if input(f"[{APP}] {text} Install now? [Y/n] ").strip().lower() not in (
            "",
            "y",
            "yes",
        ):
            say("Nothing installed.")
            raise SystemExit(0)

    def fixes(tk=False):
        packages = {
            "Debian / Ubuntu": "sudo apt install python3 python3-venv python3-tk",
            "Fedora": "sudo dnf install python3 python3-tkinter",
            "Arch": "sudo pacman -S python tk",
            "openSUSE": "sudo zypper install python3 python3-tk",
            "Alpine": "sudo apk add python3 py3-tkinter",
            "macOS": "brew install python python-tk",
            "Windows": "Install Python 3.11+ from python.org, including Tcl/Tk and pip.",
        }
        for name, command in packages.items():
            say(f"{name}: {command}")

    def main():
        args = sys.argv[1:]
        yes = bool(args and args[0] in ("-y", "--yes"))
        if yes:
            args = args[1:]
        if sys.version_info < (3, 11):
            say("Python 3.11 or newer is required.")
            fixes()
            return 1
        if not args or args[0] == "gui":
            try:
                import tkinter
            except ImportError:
                say("Tk is missing. Install the package for your OS:")
                fixes(True)
                return 1
        if not PY.is_file():
            ask("Create a private .venv and install the app dependencies?", yes)
            subprocess.run([sys.executable, "-P", "-m", "venv", str(ENV)], check=True)
        version = subprocess.check_output(
            [str(PY), "-P", "-c", "import sys;print(sys.version)"]
        )
        stamp = hashlib.sha256(
            (HERE / "pyproject.toml").read_bytes() + version + b"default"
        ).hexdigest()
        stampfile = ENV / ".aruco-installed"
        imports = "import aruco_simulator, cv2, trimesh, PIL, tkinterdnd2, fast_simplification, cascadio"
        good = stampfile.exists() and stampfile.read_text() == stamp
        if good:
            good = (
                subprocess.run(
                    [str(PY), "-P", "-c", imports],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
        if not good:
            ask("Install/update dependencies in the private .venv?", yes)
            subprocess.run(
                [
                    str(PY),
                    "-P",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "-e",
                    str(HERE),
                ],
                check=True,
            )
            subprocess.run([str(PY), "-P", "-c", imports], check=True)
            stampfile.write_text(stamp)
            say("Install complete. Good to go.")
        else:
            say("All dependencies present. Good to go.")
        return subprocess.call([str(PY), "-P", "-m", "aruco_simulator", *args])

    if __name__ == "__main__":
        try:
            raise SystemExit(main())
        except (OSError, subprocess.CalledProcessError) as exc:
            say(
                f"Could not start: {exc}. Check the error above and run this command again."
            )
            raise SystemExit(1)
