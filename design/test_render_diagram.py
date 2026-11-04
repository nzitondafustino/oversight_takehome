"""Chrome discovery on each OS. Windows and Linux are simulated, not run for real."""

import importlib.util
import platform
import shutil
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "render_diagram.py"
spec = importlib.util.spec_from_file_location("render_diagram", SCRIPT)
render_diagram = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_diagram)


def install(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def clean_machine(monkeypatch, tmp_path):
    """A machine with nothing on the PATH and no Chrome settings."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(render_diagram.Path, "home", lambda: tmp_path / "home")
    for name in ("CHROME_PATH", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def use_system(monkeypatch, name):
    monkeypatch.setattr(platform, "system", lambda: name)


def test_chrome_path_wins_over_everything(clean_machine, monkeypatch):
    chosen = install(clean_machine / "custom" / "my-chrome")
    on_path = install(clean_machine / "bin" / "google-chrome")
    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setenv("CHROME_PATH", str(chosen))
    assert render_diagram.find_chrome() == str(chosen)


def test_invalid_chrome_path_is_an_error_not_a_silent_fallback(clean_machine, monkeypatch):
    on_path = install(clean_machine / "bin" / "google-chrome")
    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setenv("CHROME_PATH", str(clean_machine / "missing"))
    with pytest.raises(FileNotFoundError, match="CHROME_PATH is not an executable"):
        render_diagram.find_chrome()


@pytest.mark.parametrize("command", render_diagram.CHROME_COMMANDS)
@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_path_commands_are_found_on_every_os(clean_machine, monkeypatch, system, command):
    use_system(monkeypatch, system)
    installed = install(clean_machine / "bin" / command)
    monkeypatch.setenv("PATH", str(installed.parent))
    monkeypatch.setenv("PATHEXT", "")
    assert render_diagram.find_chrome() == str(installed)


@pytest.mark.parametrize("variable", ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"])
def test_windows_install_folders(clean_machine, monkeypatch, variable):
    use_system(monkeypatch, "Windows")
    root = clean_machine / "win"
    monkeypatch.setenv(variable, str(root))
    installed = install(root / "Google" / "Chrome" / "Application" / "chrome.exe")
    assert render_diagram.find_chrome() == str(installed)


def test_mac_per_user_install(clean_machine, monkeypatch):
    use_system(monkeypatch, "Darwin")
    installed = install(clean_machine / "home" / "Applications" / "Chromium.app/Contents/MacOS/Chromium")
    locations = render_diagram._install_locations("Darwin")
    assert installed in locations
    monkeypatch.setattr(render_diagram, "_install_locations", lambda system: [installed])
    assert render_diagram.find_chrome() == str(installed)


def test_linux_checks_snap_and_opt():
    assert render_diagram._install_locations("Linux") == [Path("/opt/google/chrome/chrome"), Path("/snap/bin/chromium")]


def test_nothing_installed_names_the_os_and_what_was_searched(clean_machine, monkeypatch):
    use_system(monkeypatch, "Windows")
    monkeypatch.setenv("PROGRAMFILES", str(clean_machine / "win"))
    with pytest.raises(FileNotFoundError) as error:
        render_diagram.find_chrome()
    message = str(error.value)
    assert "on Windows" in message and "google-chrome" in message and "chrome.exe" in message


@pytest.mark.skipif(shutil.which("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") is None, reason="needs Chrome in /Applications")
def test_real_chrome_is_found_on_this_mac(monkeypatch):
    monkeypatch.delenv("CHROME_PATH", raising=False)
    assert "Chrome" in render_diagram.find_chrome() or "chrome" in render_diagram.find_chrome()
