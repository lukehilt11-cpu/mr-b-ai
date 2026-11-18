# Mr. B AI

A desktop teaching assistant (lesson plans, assignments, fresh classroom ideas) powered by Google Gemini.
Chats are saved locally. Built with Flask + pywebview, packaged with PyInstaller.

**Download:** see the project's GitHub Pages site, or the latest release.

## Build locally
- Windows: run `build.bat`
- macOS / Linux: run `./build.sh`

## Publish a release
1. In the repo, go to **Settings → Pages** and set **Source** to **GitHub Actions** (one time).
2. Tag and push:
   ```
   git tag v1.0.0
   git push origin v1.0.0
   ```
   Or open the **Actions** tab, choose the workflow, click **Run workflow**, and type a version such as `1.0.0`.
   The workflow builds Windows and macOS, creates the release, and updates the site.

If the site step fails with an "environment protection rules" error when run from a tag, go to
**Settings → Environments → github-pages** and add `v*` under *Deployment branches and tags*.
