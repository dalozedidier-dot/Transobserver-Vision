\
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def sh(cmd: List[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    if capture:
        return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return subprocess.run(cmd, check=check)


def ensure_gh_available() -> None:
    try:
        sh(["gh", "--version"], check=True, capture=True)
    except Exception as e:
        raise SystemExit(
            "GitHub CLI (gh) introuvable. Installe-le puis fais 'gh auth login'.\n"
            "Erreur: %s" % e
        )


def read_repos(repos_file: Path) -> List[str]:
    if not repos_file.exists():
        raise SystemExit(
            f"Fichier repos introuvable: {repos_file}\n"
            "Crée un fichier texte avec un repo par ligne au format owner/repo."
        )
    repos: List[str] = []
    for line in repos_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        repos.append(line)
    if not repos:
        raise SystemExit(f"Aucun repo trouvé dans {repos_file}")
    return repos


def safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", s)


def pick_last_success_run(repo: str, workflow: Optional[str], limit: int = 50) -> Optional[Dict[str, Any]]:
    cmd = ["gh", "run", "list", "-R", repo, "--limit", str(limit), "--json",
           "databaseId,conclusion,status,workflowName,displayTitle,createdAt,headBranch,headSha"]
    if workflow:
        cmd += ["--workflow", workflow]
    cp = sh(cmd, check=True, capture=True)
    data = json.loads(cp.stdout or "[]")
    for r in data:
        if (r.get("conclusion") or "").lower() == "success":
            return r
    return None


def download_run(repo: str, run_id: int, dest: Path) -> Tuple[bool, str]:
    dest.mkdir(parents=True, exist_ok=True)
    # gh run download retourne rc=1 si aucun artefact. On ne veut pas casser.
    cp = subprocess.run(["gh", "run", "download", "-R", repo, str(run_id), "-D", str(dest)],
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ok = (cp.returncode == 0)
    out = cp.stdout or ""
    return ok, out


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Collecte les artefacts des derniers runs CI et les regroupe.")
    ap.add_argument("--repos-file", default="repos.txt", help="Fichier listant les repos (owner/repo), 1 par ligne.")
    ap.add_argument("--outdir", default="_collected_reports", help="Dossier de sortie.")
    ap.add_argument("--workflow", default="", help="Nom d'un workflow a cibler. Vide = dernier run success tous workflows.")
    ap.add_argument("--zip", action="store_true", help="Crée aussi un zip final.")
    ap.add_argument("--keep", action="store_true", help="Ne supprime pas l'outdir avant exécution.")
    args = ap.parse_args()

    ensure_gh_available()

    repos_file = Path(args.repos_file).resolve()
    outdir = Path(args.outdir).resolve()
    workflow = args.workflow.strip() or None

    if outdir.exists() and not args.keep:
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    repos = read_repos(repos_file)
    ts = _dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    manifest: Dict[str, Any] = {
        "generated_utc": ts,
        "repos_file": str(repos_file),
        "workflow_filter": workflow or "",
        "items": []
    }

    for repo in repos:
        item: Dict[str, Any] = {"repo": repo, "selected_run": None, "download_ok": False, "notes": ""}
        try:
            run = pick_last_success_run(repo, workflow)
            if not run:
                item["notes"] = "Aucun run success trouvé (sur la fenêtre interrogée)."
                manifest["items"].append(item)
                continue

            run_id = int(run["databaseId"])
            wf_name = run.get("workflowName") or "workflow"
            created_at = run.get("createdAt") or ""
            item["selected_run"] = {
                "run_id": run_id,
                "workflow": wf_name,
                "createdAt": created_at,
                "headBranch": run.get("headBranch") or "",
                "headSha": (run.get("headSha") or "")[:12],
                "displayTitle": run.get("displayTitle") or "",
            }

            dest = outdir / safe_name(repo) / f"{safe_name(wf_name)}_{run_id}"
            ok, out = download_run(repo, run_id, dest)
            item["download_ok"] = ok
            if not ok:
                # souvent: "no artifacts found"
                item["notes"] = out.strip()[-500:]
            else:
                item["notes"] = "OK"
        except subprocess.CalledProcessError as e:
            item["notes"] = f"Erreur gh: {e}"
        except Exception as e:
            item["notes"] = f"Erreur: {e}"

        manifest["items"].append(item)

    write_json(outdir / "manifest.json", manifest)

    if args.zip:
        zip_path = outdir.parent / f"all_reports_bundle_{ts}.zip"
        if zip_path.exists():
            zip_path.unlink()
        import zipfile
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in outdir.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(outdir.parent))
        print(f"ZIP créé: {zip_path}")

    print(f"Terminé. Sortie: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
