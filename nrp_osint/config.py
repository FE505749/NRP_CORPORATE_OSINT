from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv
from .paths import PROJECT_ROOT, env_path, project_path


def token_file_path(path: str | os.PathLike[str] | None = None) -> Path:
    return project_path(path or os.getenv("TOKENS_FILE", "tokens.env"))


def load_tokens(path: str | os.PathLike[str] | None = None):
    token_path = token_file_path(path)
    if token_path.exists():
        load_dotenv(token_path, override=True)
    return token_path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass
class Settings:
    db_path: str = str(PROJECT_ROOT / "corporate_osint.sqlite")
    output_dir: str = str(PROJECT_ROOT / "data" / "exports")
    upload_dir: str = str(PROJECT_ROOT / "data" / "uploads")
    tokens_path: str = str(PROJECT_ROOT / "tokens.env")
    denue_local_path: str = str(PROJECT_ROOT / "data" / "denue" / "denue.sqlite")
    # Solo copia local de DENUE: sin buscadores, Clearbit, Wikidata, rastreo web ni DNS.
    denue_local_only: bool = False

    global_concurrency: int = 12
    per_host_concurrency: int = 2
    ddgs_min_interval_seconds: float = 2.5
    web_min_interval_seconds: float = 0.8
    circuit_failure_threshold: int = 5
    circuit_cooldown_seconds: int = 300

    search_provider: str = "auto"
    enable_clearbit_autocomplete: bool = True
    enable_common_crawl: bool = True
    enable_ddgs: bool = True
    enable_wikidata: bool = True
    enable_dns: bool = True

    max_search_results: int = 8
    max_company_pages: int = 10
    company_fuzzy_threshold: float = 93.0
    company_domain_accept: float = 0.82
    pattern_accept: float = 0.72
    generate_weak_email_candidates: bool = True

    request_timeout: float = 12.0
    cache_days_search: int = 21
    cache_days_page: int = 30
    cache_days_dns: int = 30
    retries: int = 3
    user_agent: str = "NRP-Corporate-OSINT-V4/4.0 public-business-research"

    @classmethod
    def from_env(cls):
        return cls(
            db_path=str(env_path("DB_PATH", "corporate_osint.sqlite")),
            output_dir=str(env_path("OUTPUT_DIR", "data/exports")),
            upload_dir=str(env_path("UPLOAD_DIR", "data/uploads")),
            tokens_path=str(token_file_path()),
            denue_local_path=str(env_path("DENUE_LOCAL_PATH", "data/denue/denue.sqlite")),
            denue_local_only=_bool("DENUE_LOCAL_ONLY", False),
            global_concurrency=int(os.getenv("GLOBAL_CONCURRENCY", "12")),
            per_host_concurrency=int(os.getenv("PER_HOST_CONCURRENCY", "2")),
            ddgs_min_interval_seconds=float(os.getenv("DDGS_MIN_INTERVAL_SECONDS", "2.5")),
            web_min_interval_seconds=float(os.getenv("WEB_MIN_INTERVAL_SECONDS", "0.8")),
            circuit_failure_threshold=int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5")),
            circuit_cooldown_seconds=int(os.getenv("CIRCUIT_COOLDOWN_SECONDS", "300")),
            search_provider=os.getenv("SEARCH_PROVIDER", "auto"),
            enable_clearbit_autocomplete=_bool("ENABLE_CLEARBIT_AUTOCOMPLETE", True),
            enable_common_crawl=_bool("ENABLE_COMMON_CRAWL", True),
            enable_ddgs=_bool("ENABLE_DDGS", True),
            enable_wikidata=_bool("ENABLE_WIKIDATA", True),
            enable_dns=_bool("ENABLE_DNS", True),
            generate_weak_email_candidates=_bool("GENERATE_WEAK_EMAIL_CANDIDATES", True),
            company_fuzzy_threshold=float(os.getenv("COMPANY_FUZZY_THRESHOLD", "93")),
            company_domain_accept=float(os.getenv("COMPANY_DOMAIN_ACCEPT", "0.82")),
            pattern_accept=float(os.getenv("PATTERN_ACCEPT", "0.72")),
        )


def tokens():
    return {
        "denue": os.getenv("INEGI_DENUE_TOKEN", "").strip(),
        "searxng": os.getenv("SEARXNG_URL", "").strip(),
    }
