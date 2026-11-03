# Cognix interface for Paper Trail

This frontend is adapted from the Cognix UI and reads documents, graph data, review items, and graph grounded answers from Paper Trail's local review API.

## Run locally

1. Start the Paper Trail review server from the repository root with `python -m backend.app.review.server`.
2. Copy the printed review token URL and open it in place of `http://127.0.0.1:8766` with port `5173` while preserving the `#token=...` fragment. For example: `http://127.0.0.1:5173/#token=YOUR_TOKEN`.
3. In this directory, run `npm ci` once and then `npm run dev`.

The Vite development server forwards `/api` requests to `http://127.0.0.1:8765`. Set `PAPERTRAIL_API_TARGET` if the review server uses another local address. New files are currently added through Paper Trail's watched folder; direct browser uploads are not exposed by the API.

The API does not currently expose watcher status. Analytics are computed from the live document and graph endpoints. Some advanced Cognix areas, such as settings, contradictions, and Neo4j administration, are still placeholders and are not backed by Paper Trail endpoints.
