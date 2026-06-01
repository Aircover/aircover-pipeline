# Publishing `aircover-pipeline` to PyPI

This document covers:

1. **One-time setup** (PyPI account, Trusted Publishing config, GitHub Environment).
2. **Per-release process** for cutting a new version.
3. **Troubleshooting** for common publish failures.

Releases are automated via GitHub Actions Trusted Publishing — no PyPI API tokens are stored as GitHub secrets. Every `v*` tag push to `main` triggers a publish.

---

## One-time setup (do once per package)

### 1. Create a PyPI account

1. Go to <https://pypi.org/account/register/>.
2. Use an email that's actively monitored — PyPI sends publish failures and security alerts here. A shared `support@aircover.ai` mailbox is ideal so it doesn't depend on one person.
3. Verify the email.
4. **Enable 2FA** (PyPI requires it for publishing). Use a TOTP app (1Password, Authy, etc.) or a hardware key.

### 2. Configure Trusted Publishing on PyPI

Trusted Publishing lets GitHub Actions publish to PyPI using short-lived OIDC tokens instead of long-lived API tokens. Nothing sensitive lives in GitHub Secrets.

1. Sign in to PyPI.
2. Go to <https://pypi.org/manage/account/publishing/>.
3. Under **"Add a new pending publisher,"** fill in:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `aircover-pipeline` |
   | Owner | `Aircover` |
   | Repository name | `aircover-pipeline` |
   | Workflow filename | `publish.yml` |
   | Environment name | `pypi` |

4. Click **Add**.

This creates a "pending publisher" tied to the project name. PyPI will accept the first publish from the matching GitHub workflow and convert the pending publisher into an active one.

### 3. Create the `pypi` GitHub Environment

The publish workflow references `environment: pypi`, which has to exist in the repo's environment settings.

1. Go to <https://github.com/Aircover/aircover-pipeline/settings/environments>.
2. Click **New environment**, name it `pypi`, click **Configure environment**.
3. *(Optional but recommended)* Add a required reviewer under "Deployment protection rules." Anyone listed must approve before the publish runs. Use this if you want a manual gate before every PyPI push.
4. Save.

That's the entire one-time setup. From here on, releases are just `git tag` + `git push --tags`.

---

## Per-release process

For every release (after `v1.0.3`, which is the first one):

1. **Bump the version in two places** (CI doesn't currently enforce this — be careful):
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `aircover_client.py` → `__version__ = "X.Y.Z"`

2. **Update `CHANGELOG.md`** — move items from `[Unreleased]` into a new versioned section with today's date. Add the new compare link at the bottom.

3. **Commit and push to `main`**:

   ```sh
   git add pyproject.toml aircover_client.py CHANGELOG.md
   git commit -m "Release vX.Y.Z"
   git push
   ```

   Wait for CI (the `Test` job) to pass.

4. **Tag and push the tag**:

   ```sh
   git tag vX.Y.Z -m "Release X.Y.Z"
   git push --tags
   ```

   Note: `git push` alone does NOT push tags. The explicit `--tags` flag is required.

5. **Watch the publish workflow** at <https://github.com/Aircover/aircover-pipeline/actions>. It typically takes ~1 minute.

6. **Verify on PyPI** at <https://pypi.org/project/aircover-pipeline/>. Should show the new version within ~2 minutes.

7. *(Optional)* **Create a GitHub Release** from the tag with the changelog excerpt as release notes. Gives customers a structured update feed they can subscribe to.

---

## First release (cutting v1.0.3 to PyPI)

The package source is already at `v1.0.3` — no version bump needed. To publish:

```sh
git tag v1.0.3 -m "Release 1.0.3 — initial PyPI publication"
git push --tags
```

Watch the workflow, verify on PyPI. Once it succeeds, the pending publisher becomes an active one and future releases are fully automated.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Workflow fails: "Trusted publisher not configured for project" | Pending publisher mismatch on PyPI | Double-check Owner, Repository, Workflow filename, and Environment name in PyPI publisher config exactly match the workflow file. Case-sensitive. |
| Workflow fails: "id-token: write permission required" | Missing `permissions:` block | Already present in `publish.yml`; if you forked or copied, ensure it's there |
| `twine check` fails on the README | README has markdown PyPI doesn't render | Run `twine check dist/*` locally before tagging. Avoid HTML, raw URLs, or non-standard markdown extensions |
| Tag pushed but workflow didn't trigger | Tag wasn't actually pushed | `git push` alone doesn't include tags. Use `git push --tags` (or `git push origin vX.Y.Z`) |
| "File already exists" / version conflict on upload | `pyproject.toml` and `aircover_client.py` versions don't match | Both must say the same `X.Y.Z`. CI doesn't enforce this yet — see backlog |
| `pip install aircover-pipeline` shows old version after publish | PyPI CDN cache | Wait 2–3 minutes; retry. Force a fresh check with `pip install --no-cache-dir aircover-pipeline` |

---

## Migrating later (org rename, transfer, etc.)

If `aircover-pipeline` ever moves to a different GitHub org (e.g., from `Aircover` to a renamed `aircover` org):

1. Transfer the repo via GitHub Settings → Danger Zone.
2. Visit your project's publishing settings on PyPI (`/manage/project/aircover-pipeline/settings/publishing/`).
3. Update the **Owner** field of the trusted publisher to the new org name. Save.
4. The PyPI package name stays the same. Customers' `pip install aircover-pipeline` keeps working. The old GitHub URLs auto-redirect.

The only thing that changes is the URLs in `pyproject.toml` — bump those in the next release.
