"""Convert an HTML page to a PNG using headless Chrome.

Run from anywhere: python design/render_diagram.py
With no arguments it renders design/offline_policy_compiler.html next to itself.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "offline_policy_compiler.html"

CHROME_COMMANDS = ["google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"]


def _install_locations(system: str) -> list[Path]:
    """Where Chrome and Chromium are normally installed on this operating system."""
    if system == "Darwin":
        return [
            root / app
            for root in (Path("/Applications"), Path.home() / "Applications")
            for app in ("Google Chrome.app/Contents/MacOS/Google Chrome", "Chromium.app/Contents/MacOS/Chromium")
        ]
    if system == "Windows":
        roots = [os.environ.get(name) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        return [
            Path(root) / app
            for root in roots
            if root
            for app in ("Google/Chrome/Application/chrome.exe", "Chromium/Application/chrome.exe")
        ]
    return [Path("/opt/google/chrome/chrome"), Path("/snap/bin/chromium")]


def find_chrome() -> str:
    """Return the Chrome to use: $CHROME_PATH, then the PATH, then the OS install locations."""
    override = os.environ.get("CHROME_PATH")
    if override:
        found = shutil.which(override)
        if found:
            return found
        raise FileNotFoundError(f"CHROME_PATH is not an executable: {override}")

    for command in CHROME_COMMANDS:
        found = shutil.which(command)
        if found:
            return found

    system = platform.system()
    locations = _install_locations(system)
    for location in locations:
        found = shutil.which(location)
        if found:
            return found

    searched = ", ".join(CHROME_COMMANDS + [str(location) for location in locations])
    raise FileNotFoundError(f"Chrome not found on {system}, searched: {searched}. Set CHROME_PATH to its location.")


def html_to_png(html: Path, png: Path, width: int = 1900, height: int = 945, scale: int = 2) -> Path:
    """Render `html` to `png`. The image is width*scale by height*scale pixels."""
    html, png = html.resolve(), png.resolve()
    if not html.is_file():
        raise FileNotFoundError(f"HTML file not found: {html}")
    png.parent.mkdir(parents=True, exist_ok=True)

    # Written beside the target and swapped in at the end, so a failed render
    # leaves any existing PNG untouched and cannot pass as a success.
    with tempfile.TemporaryDirectory() as profile, tempfile.TemporaryDirectory(dir=png.parent) as staging:
        rendered = Path(staging) / png.name
        result = subprocess.run(
            [
                find_chrome(),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                f"--force-device-scale-factor={scale}",
                f"--screenshot={rendered}",
                html.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0 or not rendered.is_file() or rendered.stat().st_size == 0:
            raise RuntimeError(f"Chrome did not produce a PNG (exit {result.returncode}):\n{result.stderr.strip()}")
        rendered.replace(png)
    return png


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("html", nargs="?", type=Path, default=DEFAULT_INPUT, help="HTML file to render")
    parser.add_argument("-o", "--output", type=Path, help="PNG path (default: the HTML path with a .png extension)")
    parser.add_argument("--width", type=int, default=1900, help="page width in CSS pixels")
    parser.add_argument("--height", type=int, default=945, help="page height in CSS pixels")
    parser.add_argument("--scale", type=int, default=2, help="pixel density of the image")
    args = parser.parse_args()

    try:
        png = html_to_png(args.html, args.output or args.html.with_suffix(".png"), args.width, args.height, args.scale)
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
