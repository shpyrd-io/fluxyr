# Releases

The distribution, Python import and CLI are all named `fluxyr`.
`fluxyr/_version.py` is the only version source. Setuptools reads it when building;
`fluxyr --version`, `fluxyr.__version__`, `/api/health` and the workbench's
ENGINE VERSION report that same value. The frontend has no independent version.

Use semantic versions: patch for fixes, minor for features, major for breaking
changes after 1.0. During 0.x, document breaking changes in each minor release.
Never replace an already published version; issue a new version for changes.

## Publish

1. Update `fluxyr/_version.py` and `CHANGELOG.md` in a reviewed commit on `main`,
   then push that commit.
2. Wait for CI (Python 3.11/3.13, fast SQLite tests, UI tests/build, package checks and
   an installed-wheel consumer test).
3. Tag that commit and push the tag:

   ```sh
   git tag -a v0.9.2 -m 'Fluxyr 0.9.2'
   git push origin v0.9.2
   ```

The Release workflow checks the tag/version and main ancestry, runs CI plus the full PostgreSQL/Linux integration suite,
then publishes a GitHub release with the tested wheel, sdist and SHA256SUMS.
The integration gate also builds the optional browser Docker target and tests
private input and TOTP using Chromium against local fixtures. Consumers of the
wheel do not need Node unless enabling the optional browser. Installing from a Git checkout requires
building the UI first; use the released wheel for a minimal consumer application.

For a failed publication, retry the workflow run. To dispatch against an existing
tag, use `gh workflow run release.yml --ref v0.9.2 -f tag=v0.9.2`.
Manual dispatch must use the tag as its ref so tests and artifacts match the tag.

## One-time PyPI setup

Before the first upload, a maintainer must register a **pending trusted publisher**
at https://pypi.org/manage/account/publishing/ using:

- Project name: `fluxyr`
- Owner: `shpyrd-io`
- Repository: `fluxyr`
- Workflow: `release.yml`
- Environment: `pypi`

Create the matching `pypi` environment in GitHub repository settings and optionally
require maintainer approval. Then enable the repository Actions variable
`PYPI_PUBLISH=true`. The release workflow publishes using short-lived OIDC
credentials; no PyPI API token is stored in the repository. Until enabled, GitHub
releases work independently and the PyPI job is explicitly skipped.
A missing public PyPI project does not reserve its name; PyPI decides availability
at first publication. Check the PyPI job before announcing index availability.

## requirements.txt

Once published to PyPI:

```text
fluxyr==0.1.0
```

A GitHub release wheel also works without PyPI:

```text
fluxyr @ https://github.com/shpyrd-io/fluxyr/releases/download/v0.1.0/fluxyr-0.1.0-py3-none-any.whl
```

Both forms install the bundled UI and dependencies. Pin versions for deployments.
