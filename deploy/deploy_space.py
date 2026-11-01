"""
Publish the live planner API to a free Hugging Face Docker Space.

    python deploy/deploy_space.py --user <hf-username>             # create or update the Space
    python deploy/deploy_space.py --stage-only --stage-dir build/space   # just assemble the files (for docker build)

The token is read from the HF_TOKEN environment variable (a Write token from
huggingface.co/settings/tokens); it is never written to any file. After the
upload, web/assets/js/config.js is pointed at the Space so the website's
planner form talks to it.
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPACE_FILES = ROOT / "deploy" / "hf-space"
COPY_DIRS = ["server", "src", "config"]
COPY_FILES = ["outputs/ml_results/metrics.json"]  # the VIT-AP GA-tuned Random Forest settings the planner reuses
CONFIG_JS = ROOT / "web" / "assets" / "js" / "config.js"


def stage(dest: Path) -> Path:
    """Assemble the Space's files in dest: Dockerfile, README, requirements and the code it runs."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SPACE_FILES, dest)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for d in COPY_DIRS:
        shutil.copytree(ROOT / d, dest / d, ignore=ignore)
    for f in COPY_FILES:
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dest / f)
    return dest


def space_url(user: str, space: str) -> str:
    return f"https://{re.sub(r'[^a-z0-9-]', '-', f'{user}-{space}'.lower())}.hf.space"


def point_website_at(url: str) -> None:
    text = CONFIG_JS.read_text(encoding="utf-8")
    new = re.sub(r'^window\.EV_API = ".*?";', f'window.EV_API = "{url}";', text, count=1, flags=re.M)
    CONFIG_JS.write_text(new, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", help="Hugging Face username (owner of the Space)")
    ap.add_argument("--space", default="ev-charging-planner", help="Space name (default ev-charging-planner)")
    ap.add_argument("--stage-only", action="store_true", help="Only assemble the files, do not upload")
    ap.add_argument("--stage-dir", help="Where to assemble the files (default: a temporary folder)")
    args = ap.parse_args()

    dest = Path(args.stage_dir) if args.stage_dir else Path(tempfile.mkdtemp()) / "space"
    stage(dest)
    print(f"Staged the Space files in {dest}")
    if args.stage_only:
        return 0

    token = os.environ.get("HF_TOKEN")
    if not args.user or not token:
        print("Set HF_TOKEN (a Write token) and pass --user <hf-username>.")
        return 2
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    repo_id = f"{args.user}/{args.space}"
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=str(dest),
                      commit_message="Update the live planner API", delete_patterns=["server/**", "src/**", "config/**"])
    url = space_url(args.user, args.space)
    point_website_at(url)
    print(f"Space: https://huggingface.co/spaces/{repo_id}")
    print(f"API:   {url}/api/health  (the first build takes a few minutes)")
    print(f"web/assets/js/config.js now points at {url}")
    print("Optional: add OCM_API_KEY under the Space's Settings -> Variables and secrets -> New secret.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
