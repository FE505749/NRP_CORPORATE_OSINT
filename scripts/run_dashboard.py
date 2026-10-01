"""Arranque portable de NRP Intelligence para macOS y Windows.

Levanta el servidor local (FastAPI + uvicorn, api/main.py), espera a que
responda y abre el navegador. Mismo esquema que skill-diagramas-visio.
"""

from pathlib import Path
import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORT_FILE = PROJECT_ROOT / "data" / "cache" / "dashboard_port.txt"


def remembered_port() -> int | None:
    """Puerto usado por nuestra propia instancia en el arranque anterior."""
    try:
        return int(PORT_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def remember_port(port: int) -> None:
    try:
        PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PORT_FILE.write_text(str(port), encoding="utf-8")
    except OSError:
        pass


# Directo, sin el proxy de Windows: con el proxy corporativo, una consulta a
# 127.0.0.1 sale a la red y vuelve con 403, y el lanzador nunca veria el servidor.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def app_is_running(url: str) -> bool:
    """Solo cuenta un servidor que responda como NRP Intelligence."""
    try:
        with _LOCAL.open(f"{url}/api/salud", timeout=0.8) as response:
            return response.status == 200 and b"NRP Intelligence" in response.read()
    except Exception:
        return False


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def available_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 10):
        if port_is_free(host, port):
            return port
    raise RuntimeError(f"No hay un puerto disponible entre {preferred} y {preferred + 9}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia NRP Intelligence en el navegador local.")
    parser.add_argument("--check", action="store_true", help="Valida el lanzador sin iniciar el servidor.")
    parser.add_argument("--no-browser", action="store_true", help="No abre el navegador.")
    args = parser.parse_args()

    token_value = os.getenv("TOKENS_FILE", "tokens.env")
    token_path = Path(os.path.expandvars(token_value)).expanduser()
    if not token_path.is_absolute():
        token_path = PROJECT_ROOT / token_path
    load_dotenv(token_path, override=False)

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("FastAPI o uvicorn no estan instalados. Ejecuta primero el instalador de tu sistema.", file=sys.stderr)
        return 1

    host = os.getenv("NRP_HOST", "127.0.0.1")
    try:
        preferred_port = int(os.getenv("NRP_PORT", "8501"))
    except ValueError:
        print("NRP_PORT debe ser un numero entero.", file=sys.stderr)
        return 1

    preferred_url = f"http://{host}:{preferred_port}"
    if args.check:
        print(f"Lanzador OK | {sys.platform} | Python {sys.version_info.major}.{sys.version_info.minor} | {preferred_url}")
        return 0

    # Solo reutilizamos un servidor que sea este programa.
    known = remembered_port()
    if known is not None:
        known_url = f"http://{host}:{known}"
        if app_is_running(known_url):
            print(f"NRP Intelligence ya esta activo en {known_url}")
            if not args.no_browser:
                webbrowser.open(known_url)
            return 0

    port = available_port(host, preferred_port)
    if port != preferred_port:
        print(f"El puerto {preferred_port} esta ocupado por otro programa. Usando {port}.")
    remember_port(port)
    url = f"http://{host}:{port}"
    command = [sys.executable, "-m", "uvicorn", "api.main:app", "--host", host, "--port", str(port)]
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    proceso = subprocess.Popen(command, cwd=PROJECT_ROOT, env=environment)
    print(f"NRP Intelligence en {url}  (Ctrl+C para detener)")
    try:
        for _ in range(120):
            if proceso.poll() is not None:
                return proceso.returncode
            if app_is_running(url):
                if not args.no_browser:
                    webbrowser.open(url)
                break
            time.sleep(0.25)
        return proceso.wait()
    except KeyboardInterrupt:
        proceso.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
