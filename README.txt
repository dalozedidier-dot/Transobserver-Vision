Collect all reports

Fichiers:
- .github/workflows/collect_all_reports.yml
- scripts/collect_all_reports.py
- repos.txt

Usage (GitHub web):
Actions -> Collect all reports -> Run workflow

Recommandation token:
Créer un secret GH_PAT dans le repo qui exécute ce workflow.
Il doit avoir accès aux repos listés et la permission Actions en lecture (download) suffit,
mais selon les règles GitHub il peut être nécessaire de lui donner plus.

Sorties:
- _collected_reports/manifest.json
- _collected_reports/<owner>__<repo>/run_<id>/artifacts/*
- all_reports_bundle_<timestamp>.zip (si make_zip = true)
