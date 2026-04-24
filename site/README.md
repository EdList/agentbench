# AgentBench Landing Page

This is the source for the AgentBench landing site at [agentbench.dev](https://agentbench.dev).

## Deploy via GitHub Pages

1. Push the `site/` directory to your repository (e.g. on a `gh-pages` branch or in `main`).

2. In your GitHub repo, go to **Settings → Pages**.

3. Under **Source**, choose:
   - **Branch**: `main` (or `gh-pages`)
   - **Folder**: `/site` (or `/ (root)` if the site files live at the repo root)

4. Click **Save**. GitHub will build and deploy the site.

5. In your DNS provider, add a `CNAME` record pointing `agentbench.dev` to `<username>.github.io`.

6. (Optional) Check **Enforce HTTPS** in the Pages settings once the certificate is issued.

The `CNAME` file in this directory tells GitHub Pages to serve the site at `agentbench.dev`.

## Local Preview

```bash
# Python
cd site && python -m http.server 8080

# Node
npx serve site
```

Then open [http://localhost:8080](http://localhost:8080).

## Files

| File | Purpose |
|------|---------|
| `index.html` | Single-page landing site (embedded CSS + JS) |
| `CNAME` | Custom domain for GitHub Pages |
| `README.md` | This file |
