#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_capture(cmd: List[str], *, cwd: Optional[Path] = None) -> Tuple[int, str]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return p.returncode, p.stdout or ""


def ensure_gh() -> None:
    rc, out = run_capture(["gh", "--version"])
    if rc != 0:
        raise SystemExit("gh introuvable sur le runner.\n" + out)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class WorkflowTarget:
    repo: str
    name: Optional[str] = None
    file: Optional[str] = None


@dataclass
class RepoTarget:
    repo: str
    workflows: List[WorkflowTarget]


def load_targets(path: Path) -> List[RepoTarget]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: List[RepoTarget] = []
    for t in cfg.get("targets", []):
        repo = str(t["repo"])
        wfs: List[WorkflowTarget] = []
        for w in t.get("workflows", []):
            wfs.append(WorkflowTarget(repo=repo, name=w.get("name"), file=w.get("file")))
        out.append(RepoTarget(repo=repo, workflows=wfs))
    return out


def workflow_selector(wf: WorkflowTarget) -> Optional[str]:
    return wf.file or wf.name


def gh_workflow_run(owner: str, repo: str, wf: WorkflowTarget) -> Tuple[bool, str]:
    selector = workflow_selector(wf)
    if not selector:
        return False, "workflow_selector_missing"
    cmd = ["gh", "workflow", "run", selector, "-R", f"{owner}/{repo}"]
    rc, out = run_capture(cmd)
    return rc == 0, out.strip()


def gh_find_latest_run_id(owner: str, repo: str, wf: WorkflowTarget, timeout_s: int = 90) -> Optional[int]:
    selector = workflow_selector(wf)
    if not selector:
        return None

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cmd = [
            "gh",
            "run",
            "list",
            "-R",
            f"{owner}/{repo}",
            "--workflow",
            selector,
            "--limit",
            "5",
            "--json",
            "databaseId,createdAt,status,conclusion,displayTitle,htmlUrl",
        ]
        rc, out = run_capture(cmd)
        if rc == 0:
            try:
                data = json.loads(out or "[]")
            except Exception:
                data = []
            if data:
                return int(data[0]["databaseId"])
        time.sleep(3)
    return None


def gh_wait_run(owner: str, repo: str, run_id: int, poll_s: int = 10, timeout_s: int = 5400) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        cmd = [
            "gh",
            "run",
            "view",
            str(run_id),
            "-R",
            f"{owner}/{repo}",
            "--json",
            "databaseId,status,conclusion,createdAt,updatedAt,htmlUrl",
        ]
        rc, out = run_capture(cmd)
        if rc != 0:
            last = {"error": "gh_run_view_failed", "output": out.strip()}
            time.sleep(poll_s)
            continue

        try:
            last = json.loads(out)
        except Exception:
            last = {"error": "json_parse_failed", "output": out.strip()}

        if last.get("status") == "completed":
            return last

        time.sleep(poll_s)

    last["error"] = "timeout"
    return last


def gh_download_artifacts(owner: str, repo: str, run_id: int, dest: Path) -> Tuple[bool, str]:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["gh", "run", "download", str(run_id), "-R", f"{owner}/{repo}", "-D", str(dest)]
    rc, out = run_capture(cmd)
    return rc == 0, out.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Orchestrateur: déclenche et attend plusieurs workflows cross-repo.")
    ap.add_argument("--config", default="targets.yml", help="Fichier YAML listant les repos et workflows")
    ap.add_argument("--owner", default="dalozedidier-dot", help="Owner GitHub")
    ap.add_argument("--out", default="_orchestrate_out", help="Dossier de sortie")
    ap.add_argument("--keep-going", action="store_true", help="Continuer même si un workflow échoue")
    ap.add_argument("--download-artifacts", action="store_true", help="Télécharger les artefacts des runs")
    args = ap.parse_args()

    ensure_gh()

    cfg_path = Path(args.config).resolve()
    out_dir = Path(args.out).resolve()
    targets = load_targets(cfg_path)

    summary: Dict[str, Any] = {
        "utc_start": now_utc(),
        "owner": args.owner,
        "config": str(cfg_path),
        "runs": [],
    }

    any_fail = False

    for rt in targets:
        for wf in rt.workflows:
            label = workflow_selector(wf) or "unknown"
            run_rec: Dict[str, Any] = {
                "repo": rt.repo,
                "workflow": label,
                "utc_start": now_utc(),
                "trigger": {},
                "run": {},
            }

            ok, out = gh_workflow_run(args.owner, rt.repo, wf)
            run_rec["trigger"] = {"ok": ok, "output": out}

            if not ok:
                any_fail = True
                run_rec["utc_end"] = now_utc()
                summary["runs"].append(run_rec)
                if not args.keep_going:
                    summary["utc_end"] = now_utc()
                    summary["overall_rc"] = 1
                    write_json(out_dir / "summary.json", summary)
                    return 1
                continue

            run_id = gh_find_latest_run_id(args.owner, rt.repo, wf, timeout_s=90)
            if run_id is None:
                any_fail = True
                run_rec["run"] = {"error": "run_id_not_found"}
                run_rec["utc_end"] = now_utc()
                summary["runs"].append(run_rec)
                if not args.keep_going:
                    break
                continue

            run_rec["run"]["id"] = run_id
            view = gh_wait_run(args.owner, rt.repo, run_id, poll_s=10, timeout_s=5400)
            run_rec["run"]["view"] = view

            concl = (view.get("conclusion") or "").lower()
            if concl not in ("success", ""):
                any_fail = True

            if args.download_artifacts:
                dest = out_dir / "artifacts" / rt.repo / f"{label}_{run_id}"
                dl_ok, dl_out = gh_download_artifacts(args.owner, rt.repo, run_id, dest)
                run_rec["run"]["download"] = {"ok": dl_ok, "dest": str(dest), "output": dl_out}

            run_rec["utc_end"] = now_utc()
            summary["runs"].append(run_rec)

            if (view.get("status") == "completed") and ((view.get("conclusion") or "").lower() != "success") and not args.keep_going:
                break

    summary["utc_end"] = now_utc()
    summary["overall_rc"] = 1 if any_fail else 0
    write_json(out_dir / "summary.json", summary)

    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
