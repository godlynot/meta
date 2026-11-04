# Tests

End-to-end tests against a running server. They call the real Meta Model API
(a full run costs well under $1).

```bash
tests/make_media.sh
META_API_KEY=... JOBS_PER_HOUR_PER_IP=50 uvicorn watchonce.app:app --port 8077 &
python tests/api_test.py http://localhost:8077 tests/media     # every endpoint, upload formats, error paths
node tests/ui_test.js http://localhost:8077 tests/media        # every button and flow in a real browser (Playwright)

# abuse limits, against a server started with tight limits
MAX_UPLOAD_MB=1 MAX_VIDEO_S=6 JOBS_PER_HOUR_PER_IP=3 uvicorn watchonce.app:app --port 8078 &
python tests/api_test.py http://localhost:8078 tests/media --limits
```
