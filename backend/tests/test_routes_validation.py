"""Teste pentru validarea proiectului si filtrul server/client din astran_sandbox/routes.py.

routes.py importa fastapi/pydantic la nivel de modul, deci acest fisier e SARIT automat
daca acele pachete nu sunt instalate (vezi pytest.importorskip mai jos) - in mediul de
analiza curent (fara acces la retea pentru pip install) acest fisier NU a putut fi rulat;
vezi raportul final pentru instructiunile exacte de rulare intr-un mediu de dezvoltare
normal (`pip install -r requirements.txt pytest`).

    cd backend && python3 -m pytest tests/test_routes_validation.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="pip install -r requirements.txt (fastapi, pydantic)")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astran_sandbox import routes  # noqa: E402


class PathValidationTests:
    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError):
            routes._validate_path("../../etc/passwd", "file")

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError):
            routes._validate_path("/etc/passwd", "file")

    def test_rejects_double_slash(self):
        with pytest.raises(ValueError):
            routes._validate_path("a//b", "file")

    def test_rejects_dot_segment(self):
        with pytest.raises(ValueError):
            routes._validate_path("a/./b", "file")

    def test_accepts_normal_nested_path(self):
        assert routes._validate_path("scripts/combat/main.lua", "file") == "scripts/combat/main.lua"


class ServerClientTargetFilterTests:
    """Confirma ce am gasit in Faza 1: fisierele .lua/.luau cu target="client" sunt
    EXCLUSE din bundle-ul trimis spre sandbox - nu exista inca niciun runtime care sa le
    execute pe client, deci ele nu ruleaza nicaieri in prezent (doar stocate/afisate in
    editor). Testul asta e si o plasa de siguranta: daca cineva adauga mai tarziu un
    runtime client si uita sa excluda corect target="client" de pe server, testul pica."""

    def test_server_target_file_is_bundled(self):
        files = [{"path": "main.lua", "source": "print(1)", "target": "server"}]
        bundle = routes.luau_bundle(files)
        assert bundle == [{"name": "main", "source": "print(1)"}]

    def test_client_target_file_is_excluded(self):
        files = [{"path": "main.lua", "source": "print(1)", "target": "client"}]
        assert routes.luau_bundle(files) == []

    def test_missing_target_defaults_to_server_for_legacy_games(self):
        files = [{"path": "main.lua", "source": "print(1)"}]  # joc vechi, fara camp target
        assert routes.luau_bundle(files) == [{"name": "main", "source": "print(1)"}]

    def test_unknown_target_value_defaults_to_server(self):
        files = [{"path": "main.lua", "source": "print(1)", "target": "haxx"}]
        assert routes.luau_bundle(files) == [{"name": "main", "source": "print(1)"}]

    def test_non_luau_files_are_never_bundled_regardless_of_target(self):
        files = [{"path": "notes.txt", "source": "print(1)", "target": "server"}]
        assert routes.luau_bundle(files) == []


class ValidateProjectTests:
    def test_detects_duplicate_paths(self):
        with pytest.raises(ValueError):
            routes.validate_project(
                [{"path": "a.lua", "source": "x"}, {"path": "a.lua", "source": "y"}], []
            )

    def test_detects_file_folder_name_conflict(self):
        with pytest.raises(ValueError):
            routes.validate_project([{"path": "a", "source": "x"}], ["a"])

    def test_detects_file_containing_another_file(self):
        with pytest.raises(ValueError):
            routes.validate_project(
                [{"path": "a", "source": "x"}, {"path": "a/b.lua", "source": "y"}], []
            )

    def test_well_formed_project_passes(self):
        files, folders = routes.validate_project(
            [{"path": "scripts/main.lua", "source": "print(1)", "target": "server"}],
            ["scripts"],
        )
        assert folders == ["scripts"]
        assert files[0]["target"] == "server"