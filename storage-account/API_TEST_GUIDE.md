# File Storage API Test Guide

## Base URL

- `http://localhost:8080/storage`

## Auth (required for every API call)

Use these headers:

```bash
-H "X-Storage-Key: <your-access-key>" -H "X-Storage-Secret: <your-secret>"
```

Example variables:

```bash
BASE_URL="http://localhost:8080/storage"
KEY="my-access-key-001"
SECRET="my-super-secret-001"
```

---

## CREATE FOLDER

```bash
curl -i -X POST \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  "$BASE_URL/api/storage/folder?folder=my-folder"
```

Nested folder:

```bash
curl -i -X POST \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  "$BASE_URL/api/storage/folder?folder=my-folder/sub-folder"
```

---

## UPLOAD FILE (any type)

```bash
curl -i -X POST \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  -F "folder=my-folder" \
  -F "file=@/path/to/file.pdf" \
  "$BASE_URL/api/storage"
```

Video/image/doc all work the same way:

```bash
curl -i -X POST \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  -F "folder=my-folder" \
  -F "file=@/path/to/video.mp4" \
  "$BASE_URL/api/storage"
```

---

## VIEW FILE

```bash
curl -i \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  "$BASE_URL/api/storage/my-folder/file.pdf"
```

---

## DOWNLOAD FILE

```bash
curl -OJ \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  "$BASE_URL/api/storage/my-folder/file.pdf?download=true"
```

---

## DELETE FILE

```bash
curl -i -X DELETE \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  "$BASE_URL/api/storage/my-folder/file.pdf"
```

## DELETE FOLDER (recursive)

```bash
curl -i -X DELETE \
  -H "X-Storage-Key: $KEY" -H "X-Storage-Secret: $SECRET" \
  "$BASE_URL/api/storage/my-folder"
```

---

## Browser UI

- Login page: `http://localhost:8080/storage/login`
- Use the same `STORAGE_ACCESS_KEY` and `STORAGE_SECRET` values.
- UI supports: folder create, file upload, file view/download, delete file/folder.
