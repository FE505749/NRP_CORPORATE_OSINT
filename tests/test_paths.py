from pathlib import Path

from nrp_osint.config import Settings
from nrp_osint.db import DB
from nrp_osint.paths import PROJECT_ROOT, project_path, safe_upload_name


def test_relative_paths_are_project_relative(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert project_path("data/exports") == (PROJECT_ROOT / "data" / "exports").resolve()


def test_settings_support_absolute_and_relative_paths(monkeypatch, tmp_path):
    database = tmp_path / "custom.sqlite"
    monkeypatch.setenv("DB_PATH", str(database))
    monkeypatch.setenv("OUTPUT_DIR", "salidas")
    settings = Settings.from_env()
    assert Path(settings.db_path) == database
    assert Path(settings.output_dir) == (PROJECT_ROOT / "salidas").resolve()


def test_upload_names_work_for_mac_and_windows_paths():
    assert safe_upload_name("/Users/demo/base.xlsx") == "base.xlsx"
    assert safe_upload_name(r"C:\Users\demo\base.xlsx") == "base.xlsx"
    assert safe_upload_name("base:final?.xlsx") == "basefinal.xlsx"
    assert safe_upload_name("CON.csv") == "_CON.csv"
    assert safe_upload_name("CON.foo.csv") == "_CON.foo.csv"
    assert len(safe_upload_name("ñ" * 200 + ".xlsx").encode("utf-8")) <= 180


def test_database_creates_custom_parent_directory(tmp_path):
    database = tmp_path / "nested" / "data.sqlite"
    db = DB(database)
    db.conn.close()
    assert database.exists()
