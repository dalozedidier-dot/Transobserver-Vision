#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ts_compact() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def run_capture(cmd: List[str], *, cwd: Optional[Path] = None) -> Tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout or ""


def ensure_gh() -> None:
    rc, out = run_capture(["gh", "--version"])
    if rc != 0:
        raise SystemExit("gh introuvable sur le runner.\n" + out)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_repos_file(path: Path) -> List[str]:
    repos: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        repos.append(s)
    return repos


@dataclass
class RunInfo:
    database_id: int
    status: str
    conclusion: Optional[str]
    created_at: str
    display_title: Optional[str]
    workflow_name: Optional[str]
    html_url: Optional[str]


def gh_list_runs(repo: str, workflow: str = "", limit: int = 30) -> Tuple[bool, str, List[RunInfo]]:
    cmd = [
        "gh", "run", "list",
        "-R", repo,
        "--limit", str(limit),
        "--json", "databaseId,status,conclusion,createdAt,displayTitle,workflowName,htmlUrl",
    ]
    if workflow:
        cmd += ["--workflow", workflow]

    rc, out = run_capture(cmd)
    if rc != 0:
        return False, out.strip(), []

    try:
        data = json.loads(out or "[]")
    except Exception:
        return False, "json_parse_failed", []

    runs: List[RunInfo] = []
    for r in data:
        runs.append(
            RunInfo(
                database_id=int(r.get("databaseId")),
                status=str(r.get("status") or ""),
                conclusion=r.get("conclusion"),
                created_at=str(r.get("createdAt") or ""),
                display_title=r.get("displayTitle"),
                workflow_name=r.get("workflowName"),
                html_url=r.get("htmlUrl"),
            )
        )
    return True, "ok", runs


def pick_run(runs: List[RunInfo]) -> Optional[RunInfo]:
    # Priorité: completed + success, sinon latest completed, sinon latest.
    completed = [r for r in runs if r.status == "completed"]
    success = [r for r in completed if (r.conclusion or "").lower() == "success"]
    if success:
        return success[0]
    if completed:
        return completed[0]
    return runs[0] if runs else None


def gh_download_run(repo: str, run_id: int, dest: Path) -> Tuple[bool, str]:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["gh", "run", "download", str(run_id), "-R", repo, "-D", str(dest)]
    rc, out = run_capture(cmd)
    return rc == 0, out.strip()


def zip_folder(src_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src_dir.parent))


def sanitize_repo(repo: str) -> str:
    return repo.replace("/", "__")


def main() -> int:
    ap = argparse.ArgumentParser(description="Collecte transverse des artefacts GitHub Actions (multi-repos).")
    ap.add_argument("--repos-file", required=True, help="Fichier repos.txt (owner/repo par ligne).")
    ap.add_argument("--outdir", default="_collected_reports", help="Dossier de sortie.")
    ap.add_argument("--workflow", default="", help="Filtre optionnel de workflow (nom ou fichier).")
    ap.add_argument("--zip", action="store_true", help="Créer un bundle zip final.")
    ap.add_argument("--limit", type=int, default=30, help="Nombre de runs inspectés par repo.")
    args = ap.parse_args()

    ensure_gh()

    repos_path = Path(args.repos_file).resolve()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    repos = read_repos_file(repos_path)
    manifest: Dict[str, Any] = {
        "utc_start": utc_now(),
        "repos_file": str(repos_path),
        "workflow_filter": args.workflow,
        "items": [],
    }

    for repo in repos:
        item: Dict[str, Any] = {
            "repo": repo,
            "workflow_filter": args.workflow,
            "selected_run": None,
            "download_ok": False,
            "error": None,
        }

        ok, msg, runs = gh_list_runs(repo, workflow=args.workflow, limit=args.limit)
        if not ok:
            item["error"] = f"run_list_failed:{msg}"
            manifest["items"].append(item)
            continue

        selected = pick_run(runs)
        if not selected:
            item["error"] = "no_runs_found"
            manifest["items"].append(item)
            continue

        item["selected_run"] = {
            "databaseId": selected.database_id,
            "status": selected.status,
            "conclusion": selected.conclusion,
            "createdAt": selected.created_at,
            "workflowName": selected.workflow_name,
            "displayTitle": selected.display_title,
            "htmlUrl": selected.html_url,
        }

        repo_dir = outdir / sanitize_repo(repo) / f"run_{selected.database_id}"
        repo_dir.mkdir(parents=True, exist_ok=True)

        write_json(repo_dir / "run_meta.json", item["selected_run"])
        ok_dl, out_dl = gh_download_run(repo, selected.database_id, repo_dir / "artifacts")
        write_text(repo_dir / "download.log", out_dl + "\n")

        item["download_ok"] = bool(ok_dl)
        if not ok_dl:
            item["error"] = f"download_failed:{out_dl[:2000]}"
        else:
            item["error"] = None

        manifest["items"].append(item)

    manifest["utc_end"] = utc_now()
    write_json(outdir / "manifest.json", manifest)

    if args.zip:
        zip_name = f"all_reports_bundle_{ts_compact()}.zip"
        zip_path = outdir.parent / zip_name
        zip_folder(outdir, zip_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
