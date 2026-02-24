---
name: Release preperation
about: Issue for release preparation
title: ''
labels: organization
assignees: ''

---

# ProxLB Release Checklist

## 1. Pre-Release

- [ ] All PRs for this release are merged into `main`
- [ ] All changelog fragments exist in `.changelogs/<new-version>/`
- [ ] `release_meta.yml` in `.changelogs/<new-version>/` has the release date set
- [ ] CI pipeline is green on `main` (linting + tests)
- [ ] `pytest` passes locally
- [ ] `python3 -m flake8 proxlb` passes locally

## 2. Version Bumps

Update the version string in **all** of the following files:

| File | Field | Example |
|------|-------|---------|
| `proxlb/utils/version.py` | `__version__` | `"1.2.0"` |
| `setup.py` | `version` | `"1.2.0"` |
| `helm/proxlb/Chart.yaml` | `version` | `"1.2.0"` |
| `helm/proxlb/Chart.yaml` | `appVersion` | `"v1.2.0"` |
| `helm/proxlb/values.yaml` | `image.tag` | `v1.2.0` |

### Verify consistency

```bash
grep -n '__version__' proxlb/utils/version.py
grep -n 'version=' setup.py
grep -n 'version\|appVersion' helm/proxlb/Chart.yaml
grep -n 'tag:' helm/proxlb/values.yaml
```

## 3. Changelogs

- [ ] Generate `CHANGELOG.md` from fragments using [CFC](https://github.com/gyptazy/changelog-fragments-creator)
- [ ] Update `debian/changelog` with new version entry:

```bash
dch -v <new-version> -D stable "Release <new-version>"
```

- [ ] Verify `CHANGELOG.md` and `debian/changelog` both reference the correct version and date

## 4. Commit & Tag

- [ ] Commit all version bumps and changelog updates
- [ ] Create annotated git tag:

```bash
git tag -a v<new-version> -m "Release v<new-version>"
```

- [ ] Push commit and tag:

```bash
git push origin main
git push origin v<new-version>
```

## 5. Post-Release: CI/CD Pipelines

The following pipelines are triggered automatically by the tag push:

- [ ] **Container build** (`30-pipeline-build-container.yml`): images pushed to GHCR and Docker Hub with version tag + `latest`
- [ ] **Debian package** (`40-build-and-publish-deb-package.yml`): `.deb` built in stable mode, publish webhook triggered
- [ ] **RPM package** (`20-pipeline-build-rpm-package.yml`): `.rpm` artifact built

## 6. Post-Release: Artifact Verification

### Container images

- [ ] GHCR image pullable:

```bash
docker pull ghcr.io/credativ/proxlb:v<new-version>
```

- [ ] Docker Hub image pullable:

```bash
docker pull credativ/proxlb:v<new-version>
```

### Debian package

- [ ] `.deb` package installable from published repo or CI artifact

### Helm chart

- [ ] Helm chart renders correctly:

```bash
helm template proxlb helm/proxlb/
```

## 7. Post-Release: Sanity Check

- [ ] `proxlb --version` shows correct version (reads from `proxlb/utils/version.py`)
- [ ] Explain mode produces sensible output on a test cluster

## Known Pitfalls

- **`proxlb/utils/version.py` is the source of truth for Debian builds.** The pipeline `20-pipeline-build-deb-package.yml` extracts the version from this file. If it is out of sync with `setup.py`, snapshot builds get wrong version numbers.
- **Helm `values.yaml` image tag must include the `v` prefix** (e.g. `v1.2.0`) to match the git tag. Mismatches cause deployments to pull the wrong image.
- **`debian/changelog` topmost entry must be the new version.** `dpkg-parsechangelog` reads the first entry, so an outdated top entry produces wrong package versions.
- **Container images are built for both `amd64` and `arm64`** via Docker buildx. Verify both architectures if possible.
