# LGW Lab shared repository settings

This repository holds the checks that every LGW Lab repository runs on each push and pull request.

| Check | What it finds | Result |
|---|---|---|
| Secrets | API keys, tokens and passwords in commits ([gitleaks](https://github.com/gitleaks/gitleaks)) | Fails the run |
| Large files | Any added or changed file above 10 MB | Fails the run |
| Heavy notebooks | Notebooks above 2 MB that keep cell outputs (smaller notebooks with outputs get a warning, because an executed notebook is a valid run record) | Fails the run |
| Writing | Simplified Technical English (STE) score of changed Markdown (`scripts/stecheck.py`) | Advisory only |

On private repositories a failed run shows a red mark. On public repositories a branch rule also refuses a merge that fails.

## Add the checks to a repository

Copy `templates/lab-checks.yml` to `.github/workflows/lab-checks.yml` in the repository.

To check the whole history once, open the repository's Actions tab, choose **lab-checks**, and click **Run workflow**.

## Run the checks before you commit (optional)

Copy `templates/.pre-commit-config.yaml` to the repository root, then run `pip install pre-commit` and `pre-commit install` once per clone.

## Naming

Use lowercase words joined by hyphens (`resonant-pnn-training`). Give every repository a one-line description, a README and a topic for its group: `research`, `undergrad-project` or `lab-resources`.
