# Storage Account

WAR-based private object storage service (S3-like basic behavior) running on Tomcat.

When using the UHC dev stack, this service is built and started automatically by
`resouce-creation-script.py` / `master.py`. Credentials and volume paths come from
`stack_config.py` at the repo root.

## Standalone Docker usage

1. Build the image:

```bash
docker build -t uhc-storage-account:local .
```

2. Run with credentials and a persistent upload volume:

```bash
docker run --rm \
  -p 8080:8080 \
  --env STORAGE_ACCESS_KEY=my-access-key-001 \
  --env STORAGE_SECRET=my-super-secret-001 \
  -v "$(pwd)/uploads:/data/uploads" \
  uhc-storage-account:local
```

3. App URLs:
- UI login: `http://localhost:8080/storage/login`
- API base: `http://localhost:8080/storage/api/storage`

Use the same key/secret for:
- UI login form (`Access Key`, `Secret`)
- API headers (`X-Storage-Key`, `X-Storage-Secret`)

## Kubernetes env configuration

Pass credentials as environment variables in your Deployment:

```yaml
env:
  - name: STORAGE_ACCESS_KEY
    value: "my-access-key-001"
  - name: STORAGE_SECRET
    value: "my-super-secret-001"
```
