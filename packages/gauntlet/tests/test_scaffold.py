"""Rendering a template produces a suite that conforms."""

from __future__ import annotations

import shutil

import pytest

from gauntlet.conformance import verify_suite
from gauntlet.scaffold import Placeholders, ScaffoldError, available_templates, generator, render
from gauntlet.suites import load_suite


class TestPlaceholders:
    @pytest.mark.parametrize(
        ("key", "title", "class_name"),
        [
            ("probe", "Probe", "Probe"),
            ("my_probe", "My Probe", "MyProbe"),
            ("a_b_c", "A B C", "ABC"),
        ],
    )
    def test_derived_from_key(self, key, title, class_name):
        placeholders = Placeholders.from_key(key)
        assert placeholders.title == title
        assert placeholders.class_name == class_name

    def test_class_name_is_an_identifier(self):
        # The title contains spaces and cannot be used as a class name.
        assert Placeholders.from_key("my_probe").class_name.isidentifier()


class TestAvailableTemplates:
    def test_both_templates_are_offered(self):
        assert set(available_templates()) == {"python", "shell"}

    def test_a_missing_template_directory_offers_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generator, "TEMPLATES_DIR", tmp_path / "absent")

        assert generator.available_templates() == []


class TestRender:
    @pytest.mark.parametrize("template", available_templates())
    def test_every_template_renders_a_loadable_suite(self, template, tmp_path):
        destination = render("my_probe", tmp_path, template=template)
        suite = load_suite(destination)
        assert suite.key == "my_probe"
        assert suite.manifest.title == "My Probe"

    @pytest.mark.parametrize("template", available_templates())
    def test_every_template_passes_conformance(self, template, tmp_path):
        destination = render("my_probe", tmp_path, template=template)
        report = verify_suite(destination, execute=True)
        assert report.passed, [c.name for c in report.checks if not c.passed]

    def test_no_placeholders_survive(self, tmp_path):
        destination = render("my_probe", tmp_path)
        for path in destination.rglob("*"):
            if path.is_file():
                assert "__SUITE_" not in path.read_text(), f"placeholder left in {path}"
                assert "__SUITE_" not in str(path), f"placeholder left in path {path}"

    def test_python_template_uses_the_common_package_name(self, tmp_path):
        destination = render("my_probe", tmp_path)
        runner = destination / "suite" / "runner.py"
        assert runner.is_file()
        assert "class MyProbeProfile" in runner.read_text()

    @pytest.mark.parametrize("template", available_templates())
    def test_layout_is_identical_across_templates(self, template, tmp_path):
        destination = render("my_probe", tmp_path, template=template)
        assert (destination / "suite.yaml").is_file()
        assert (destination / "profiles" / "quick.yaml").is_file()

    def test_shell_template_script_is_executable(self, tmp_path):
        import os

        destination = render("my_probe", tmp_path, template="shell")
        assert os.access(destination / "run.sh", os.X_OK)

    @pytest.mark.parametrize("key", ["MyProbe", "1probe", "my-probe", "my probe", ""])
    def test_invalid_key_is_rejected(self, key, tmp_path):
        with pytest.raises(ScaffoldError, match="lower_snake_case"):
            render(key, tmp_path)

    def test_existing_destination_is_refused(self, tmp_path):
        render("my_probe", tmp_path)
        with pytest.raises(ScaffoldError, match="already exists"):
            render("my_probe", tmp_path)

    def test_unknown_template_lists_the_available_ones(self, tmp_path):
        with pytest.raises(ScaffoldError, match="available: "):
            render("my_probe", tmp_path, template="cobol")


def test_templates_are_discovered():
    assert {"python", "shell"} <= set(available_templates())


class TestBuildArtifacts:
    def test_cache_directories_are_not_rendered(self, tmp_path, monkeypatch):
        """A packaged template can carry __pycache__; it must not be copied."""
        staged = tmp_path / "templates" / "python"
        shutil.copytree(generator.TEMPLATES_DIR / "python", staged)
        cache = staged / "suite" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "runner.cpython-310.pyc").write_bytes(b"\x00\xcc\xff not utf-8")
        monkeypatch.setattr(generator, "TEMPLATES_DIR", tmp_path / "templates")

        destination = generator.render("my_probe", tmp_path / "out")
        assert not list(destination.rglob("__pycache__"))
        assert not list(destination.rglob("*.pyc"))
