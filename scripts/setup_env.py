"""Instalador compartido por los lanzadores de macOS y Windows."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import venv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def environment_dir() -> Path:
    standard = PROJECT_ROOT / ".venv"
    other_layout = standard / ("bin/python" if os.name == "nt" else "Scripts/python.exe")
    if standard.exists() and other_layout.exists():
        platform_name = "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux"
        return PROJECT_ROOT / f".venv-{platform_name}"
    return standard


VENV_DIR = environment_dir()


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main() -> int:
    if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
        print("Se requiere Python 3.11, 3.12 o 3.13.", file=sys.stderr)
        return 1

    print(f"Python {sys.version_info.major}.{sys.version_info.minor} detectado en {sys.platform}.")
    print(f"Preparando el entorno virtual en {VENV_DIR.name}...")
    venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    python = venv_python()

    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([
        str(python), "-m", "pip", "install",
        "-r", str(PROJECT_ROOT / "requirements.txt"),
        "-c", str(PROJECT_ROOT / "constraints.txt"),
    ])

    for relative in ("data/uploads", "data/exports", "data/cache", "logs"):
        (PROJECT_ROOT / relative).mkdir(parents=True, exist_ok=True)

    tokens = PROJECT_ROOT / "tokens.env"
    if not tokens.exists():
        shutil.copyfile(PROJECT_ROOT / "tokens.example.env", tokens)

    run([str(python), str(PROJECT_ROOT / "healthcheck.py")])
    print("Instalacion terminada correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
