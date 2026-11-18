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
   The workflow builds Windows and macOS, creates the release, and updates the site.
