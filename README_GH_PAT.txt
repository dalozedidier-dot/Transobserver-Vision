Solution au 403 (ce que tu as eu dans summary.json)

Erreur observée:
- HTTP 403: Resource not accessible by integration

Cause:
- github.token (GITHUB_TOKEN) ne peut pas déclencher workflow_dispatch dans d'autres repos.
- Il faut un PAT.

À faire (dans le repo orchestrateur, ex: Vision)
1) Settings -> Secrets and variables -> Actions -> New repository secret
2) Name: GH_PAT
3) Value: PAT

PAT recommandé
- Classic PAT:
  - scope: workflow
  - scope: public_repo (si tes 5 repos sont publics) ou repo (si un est privé)
- Fine-grained PAT:
  - accès aux 5 repos
  - Actions: Read and Write
  - Contents: Read

Ensuite relancer Actions -> Orchestrate all modules -> Run workflow.
