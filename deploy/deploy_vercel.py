"""
Publish the website and the live planner API to Vercel (free Hobby plan).

    python deploy/deploy_vercel.py            # API, then the website pointed at it
    python deploy/deploy_vercel.py --web-only

Two Vercel projects:
  ev-charging-planner-api   server/app.py as a Python function (each plan runs inside one
                            request of up to 300 s and streams its progress)
  ev-charging-site-planner  the static web/ folder

The token is read from VERCEL_TOKEN and the OpenChargeMap key from OCM_API_KEY; neither is
written to any file (the key is stored as a Vercel environment variable). Needs Node.js (npx).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "ev-charging-planner-api"
API_FILES = ROOT / "deploy" / "vercel-api"
COPY_DIRS = ["server", "src", "config", "data/external/vahan"]
COPY_FILES = ["outputs/ml_results/metrics.json", "deploy/hf-space/requirements.txt"]
CONFIG_JS = ROOT / "web" / "assets" / "js" / "config.js"
NPX = "npx.cmd" if os.name == "nt" else "npx"


def vercel(args, cwd: Path, token: str, stdin: str = None) -> str:
    cmd = [NPX, "--yes", "vercel@65.9.0", *args, "--token", token]
    out = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit(f"vercel {' '.join(args)} failed:\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
    return out.stdout + out.stderr


def production_url(output: str) -> str:
    m = re.search(r'"productionUrl":\s*"([^"]+)"', output) or re.search(r"Aliased\s+(https://\S+)", output)
    if not m:
        raise SystemExit("Could not find the production URL in Vercel's output:\n" + output[-2000:])
    return m.group(1).rstrip("/")


def stage_api() -> Path:
    """build/ev-charging-planner-api: the API code, the data it reads, and its requirements (keeps .vercel/)."""
    BUILD.mkdir(parents=True, exist_ok=True)
    for item in BUILD.iterdir():
        if item.name != ".vercel":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    shutil.copytree(API_FILES, BUILD, dirs_exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for d in COPY_DIRS:
        shutil.copytree(ROOT / d, BUILD / d, ignore=ignore)
    for f in COPY_FILES:
        (BUILD / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, BUILD / f)
    shutil.move(BUILD / "deploy/hf-space/requirements.txt", BUILD / "requirements.txt")
    shutil.rmtree(BUILD / "deploy")
    (BUILD / ".python-version").write_text("3.12\n", encoding="utf-8")
    return BUILD


def deploy_api(token: str) -> str:
    build = stage_api()
    print("Deploying the planner API (installs the Python libraries; a few minutes)…")
    url = production_url(vercel(["deploy", "--prod", "--yes"], build, token))
    key = os.environ.get("OCM_API_KEY")
    if key and "OCM_API_KEY" not in vercel(["env", "ls", "production"], build, token):
        vercel(["env", "add", "OCM_API_KEY", "production"], build, token, stdin=key)
        print("Stored OCM_API_KEY as a Vercel environment variable; redeploying so the API sees it…")
        url = production_url(vercel(["deploy", "--prod", "--yes"], build, token))
    return url


def point_website_at(url: str) -> None:
    text = CONFIG_JS.read_text(encoding="utf-8")
    CONFIG_JS.write_text(re.sub(r'^window\.EV_API = ".*?";', f'window.EV_API = "{url}";', text, count=1, flags=re.M),
                         encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web-only", action="store_true", help="Only redeploy the website")
    args = ap.parse_args()
    token = os.environ.get("VERCEL_TOKEN")
    if not token:
        print("Set VERCEL_TOKEN (vercel.com/account/tokens).")
        return 2
    if not args.web_only:
        api = deploy_api(token)
        point_website_at(api)
        print(f"API:     {api}/api/health")
    print("Deploying the website…")
    site = production_url(vercel(["deploy", "web", "--prod", "--yes"], ROOT, token))
    print(f"Website: {site}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
