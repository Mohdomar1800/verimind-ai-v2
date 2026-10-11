# 0003: Server-rendered UI (Jinja2 + HTMX) instead of a JavaScript framework

**Status:** accepted

## Context
The hackathon version had two separate frontends (Streamlit and a React app) that duplicated logic.
They drifted out of sync: the React app recomputed scores in the browser and showed hard-coded mock
data on errors.

## Decision
FastAPI renders HTML pages with Jinja2 templates and plain CSS. HTMX adds partial page updates
(job progress, filtering, pagination) through HTML attributes. Chart.js draws the charts.

## Why
- **One source of truth:** all logic, including scoring, lives in Python. Templates only display it.
- **No build step:** no Node.js toolchain and no bundler, and the Docker image stays small.
- **The JSON API still exists** (`/api/...`, documented at `/api/docs`) for scripts and CI
  integrations. The HTML pages and the API call the same service layer.

## Alternatives considered
- **React/Vue single-page app**: a richer client, but it means a second language and a build
  pipeline, and it duplicates state. That's not worth it for what is mostly a set of tables, detail
  pages, and charts.
- **Streamlit**: quick for prototypes, but hard to structure, test, and customize as the app grows.

## Consequences
- Highly interactive widgets need a little vanilla JavaScript. Only the log viewer and the charts
  are expected to need any.
