# Changelog: Deployment improvements and testing documentation

## 2025-01-XX: Deployment optimization and testing guide

### Added

#### Deployment
- **`.dockerignore`**: Excludes `android/` directory from Docker build context
  - Improves build performance
  - Reduces build context size
  - Prevents accidental inclusion of Android sources in Docker images
  - Does not affect runtime (Dockerfile already copies only `backend/`)

#### Documentation
- **`docs/TESTING_STAGING_ANDROID.md`**: Comprehensive end-to-end testing guide
  - Part A: Staging server testing (CRM, `/mobile-app/`, QR-login, logout endpoints)
  - Part B: Android app testing (QR-login, polling, offline queue, graceful logout)
  - Part C: CRM functional testing (CallRequest, PhoneDevice, audit logs)
  - Troubleshooting section
  - Success criteria checklist

- **`docs/DEPLOY_AUDIT_ANDROID.md`**: Deployment audit and cleanup instructions
  - Analysis of `android/` directory usage on servers
  - Docker build context audit
  - Recommendations for cleanup (optional)
  - Verification commands

### Changed

- **Deployment process**: Now excludes `android/` from Docker build context
  - Faster builds
  - Smaller build context
  - More secure (no accidental inclusion)

### Impact

#### Runtime
- ✅ **No impact**: Dockerfile already copies only `backend/`
- ✅ **No impact**: Volumes mount only `./backend`
- ✅ **No impact**: Nginx serves only `/static/` and `/media/`

#### Build process
- ✅ **Improved**: Faster Docker builds (smaller context)
- ✅ **Improved**: More secure (explicit exclusions)
- ✅ **No breaking changes**: Existing deployment scripts work as before

#### Repository
- ✅ **No changes**: `android/` remains in git repository (for history/rollbacks)
- ✅ **No changes**: Android project structure unchanged

### Testing

#### Verification commands

```bash
# Check .dockerignore
cat .dockerignore | grep android

# Test Docker build (should be faster)
docker build -f Dockerfile.staging -t test-build .

# Verify android/ not in build context
docker build -f Dockerfile.staging -t test-build . 2>&1 | grep -i android
# Expected: empty (no android/ mentions)

# Verify runtime (android/ should not be in container)
docker compose -f docker-compose.staging.yml exec web ls -la /app/ | grep android
# Expected: empty (android/ not in container)
```

#### Deployment checklist

- [ ] `.dockerignore` committed to repository
- [ ] Test Docker build on staging
- [ ] Verify runtime works (no changes expected)
- [ ] Review testing guide: `docs/TESTING_STAGING_ANDROID.md`
- [ ] Review audit: `docs/DEPLOY_AUDIT_ANDROID.md`

### Migration notes

**No migration required.** Changes are backward compatible:
- Existing deployments continue to work
- `.dockerignore` only affects new builds
- Can be applied immediately without downtime

### Rollback

If needed, rollback is simple:
```bash
git revert <commit-hash>
# Or remove .dockerignore (but not recommended)
```

---

## Related commits

- `chore(deploy): prevent android sources from entering Docker build context`
- `docs: add staging testing plan and deployment audit`
