"""Render the Mermaid diagram sources of the documentation to PNG images.

Every `docs/assets/diagrams/<name>.mmd` file is rendered to `<name>-light.png` and
`<name>-dark.png` with Mermaid CLI, and the source digests are recorded for
`check_diagram_freshness.py`. Node.js must be installed. Set `MERMAID_BROWSER` to
the path of an installed Chromium-based browser to reuse it instead of
downloading one.

Usage:
    python scripts/render_diagrams.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from check_diagram_freshness import ROOT, check, images, sources, write_manifest

MERMAID_CLI = "@mermaid-js/mermaid-cli@12.0.0"
THEMES = {"light": "default", "dark": "dark"}
MERMAID_CONFIG = {
    "fontFamily": "'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 320},
    "sequence": {"mirrorActors": False},
}


def render() -> int:
    npx = shutil.which("npx")
    if npx is None:
        print("Node.js (npx) is required to render diagrams")
        return 1
    environment = dict(os.environ)
    puppeteer: dict[str, object] = {}
    browser = os.environ.get("MERMAID_BROWSER")
    if browser:
        puppeteer["executablePath"] = browser
        environment["PUPPETEER_SKIP_DOWNLOAD"] = "true"
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "mermaid.json"
        config.write_text(json.dumps(MERMAID_CONFIG), encoding="utf-8")
        puppeteer_config = Path(directory) / "puppeteer.json"
        puppeteer_config.write_text(json.dumps(puppeteer), encoding="utf-8")
        for source in sources():
            for (variant, theme), image in zip(
                THEMES.items(), images(source), strict=True
            ):
                subprocess.run(
                    [
                        npx,
                        "--yes",
                        MERMAID_CLI,
                        "--input",
                        str(source),
                        "--output",
                        str(image),
                        "--theme",
                        theme,
                        "--backgroundColor",
                        "transparent",
                        "--scale",
                        "2",
                        "--configFile",
                        str(config),
                        "--puppeteerConfigFile",
                        str(puppeteer_config),
                        "--quiet",
                    ],
                    check=True,
                    env=environment,
                )
                print(f"rendered {image.relative_to(ROOT).as_posix()} ({variant})")
    write_manifest()
    return check()


if __name__ == "__main__":
    sys.exit(render())
