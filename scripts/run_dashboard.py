"""Arranque portable del dashboard para macOS y Windows."""

from pathlib import Path
import argparse
import os
import socket
import subprocess
import sys
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


def skip_streamlit_welcome() -> None:
    """En el primer arranque Streamlit pide un correo por stdin y bloquea el
    lanzador. Dejamos la credencial vacia para que arranque sin preguntar."""
    credentials = Path.home() / ".streamlit" / "credentials.toml"
    if credentials.exists():
        return
    try:
        credentials.parent.mkdir(parents=True, exist_ok=True)
        credentials.write_text('[general]\nemail = ""\n', encoding="utf-8")
    except OSError:
        pass


def streamlit_is_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/_stcore/health", timeout=0.8) as response:
            return response.status == 200
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
    args = parser.parse_args()

    token_value = os.getenv("TOKENS_FILE", "tokens.env")
    token_path = Path(os.path.expandvars(token_value)).expanduser()
    if not token_path.is_absolute():
        token_path = PROJECT_ROOT / token_path
    load_dotenv(token_path, override=False)

    try:
        import streamlit  # noqa: F401
    except ImportError:
        print("Streamlit no esta instalado. Ejecuta primero el instalador de tu sistema.", file=sys.stderr)
        return 1

    skip_streamlit_welcome()

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

    # Solo reutilizamos un servidor que hayamos iniciado nosotros. Un Streamlit
    # ajeno en el puerto preferido no es este dashboard: hay que ceder el puerto.
    known = remembered_port()
    if known is not None:
        known_url = f"http://{host}:{known}"
        if streamlit_is_running(known_url):
            print(f"El dashboard ya esta activo en {known_url}")
            webbrowser.open(known_url)
            return 0

    port = available_port(host, preferred_port)
    if port != preferred_port:
        print(f"El puerto {preferred_port} esta ocupado por otro programa. Usando {port}.")
    remember_port(port)
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(PROJECT_ROOT / "dashboard.py"),
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    return subprocess.call(command, cwd=PROJECT_ROOT, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
