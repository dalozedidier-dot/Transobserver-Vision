Implantation (où mettre les fichiers)

Choisir un seul repo orchestrateur (recommandé: Vision).
Dans ce repo, à la racine:
- orchestrate_workflows.py
- targets.yml
- .github/workflows/orchestrate_all_modules.yml

Puis dans GitHub web
- Actions
- Orchestrate all modules
- Run workflow

Token
Pour déclencher et lire des runs dans d'autres repos, github.token peut être insuffisant.
Recommandé: un secret repository nommé GH_PAT (PAT avec permissions lecture Actions sur les 5 repos).
