# Dork Scout Configuration

Dork Scout is configured primarily through Python code in `src/dorkscout/config.py`, an optional `dorks.json` file for extending the curated dork database, and environment variables that override the most common settings. No external configuration file format (YAML/INI) is required, keeping the tool lightweight.

All settings are loaded at import time. The `get_*()` helper functions should be used by application code instead of importing constants directly.

## Environment Variables

The following environment variables are read using `os.getenv()` (set them in your shell, Docker, or before running `dork-scout`):

| Variable                  | Default Value                          | Description |
|---------------------------|----------------------------------------|-------------|
| `DORKS_JSON_PATH`         | `./dorks.json`                         | Path to optional JSON file that extends or overrides the embedded dork database (deep-merged). |
| `DORKSCOUT_DB_PATH`       | `./dorkscout.db`                       | Path to the SQLite persistence file used by `DiscoveryStore`. |
| `OLLAMA_URL`              | `http://localhost:11434/api/generate`  | Endpoint for the local Ollama instance (used by `generate_dorks_for_tech()` and the `/api/generate_dorks` endpoint). |
| `OLLAMA_MODEL`            | `llama3.2`                             | Ollama model name. Must be pulled locally (`ollama pull <model>`). |
| `OLLAMA_TEMPERATURE`      | `0.7`                                  | Sampling temperature for LLM dork generation. |
| `OLLAMA_TIMEOUT`          | `45.0`                                 | Timeout (seconds) for Ollama HTTP requests. |
| `SMTP_HOST`               | `smtp.example.com`                     | SMTP server for email alerts sent by `AlertScheduler`. |
| `SMTP_PORT`               | `587`                                  | SMTP port (usually 587 for TLS). |
| `SMTP_USER`               | (empty)                                | SMTP username (required for email alerts). |
| `SMTP_PASSWORD`           | (empty)                                | SMTP password or app-specific token. |
| `SMTP_FROM_EMAIL`         | `alerts@dorkscout.local`               | Sender address for email alerts. |
| `SMTP_USE_TLS`            | `true`                                 | Enable TLS for SMTP (`true`/`1`/`yes`). |
| `ALERT_COOLDOWN_MINUTES`  | `30`                                   | Minimum time between repeated alerts for the same job. |

**Example usage**

```bash
export DORKSCOUT_DB_PATH=/data/dorkscout.db
export OLLAMA_MODEL=llama3.1
export SMTP_HOST=smtp.gmail.com
export SMTP_USER=you@gmail.com
export SMTP_PASSWORD=your-app-password
export SMTP_FROM_EMAIL=alerts@yourdomain.com
dork-scout serve --host 0.0.0.0 --port 8000
```

These variables are read once when `config.py` is imported. Changing them requires restarting the process.

## Extending the Dork Database with `dorks.json`

Create a file named `dorks.json` in the project root (or set `DORKS_JSON_PATH` to another location). The loader performs a **deep merge** with the embedded `DEFAULT_DORK_DB`:

- `categories` – new or overridden category metadata.
- `dorks` – new technologies are added; additional patterns for existing technologies are appended. Duplicate `signature` values are automatically skipped.
- `tech_to_category` – mappings are merged. Every technology used in `dorks` **must** have an entry here.

### Minimal `dorks.json` Example

```json
{
  "categories": {
    "custom_payments": {
      "id": "custom_payments",
      "name": "Custom Payments",
      "icon": "🔑",
      "color": "#4f46e5",
      "description": "Niche or emerging payment processors."
    }
  },
  "dorks": {
    "NewGateway": [
      {
        "signature": "\"powered by newgateway\" OR inurl:newgateway/checkout -site:newgateway.com",
        "engine": "google"
      },
      {
        "signature": "intitle:checkout \"newgateway\"",
        "engine": "bing"
      }
    ],
    "Stripe": [
      {
        "signature": "new custom stripe pattern for europe",
        "engine": "yandex"
      }
    ]
  },
  "tech_to_category": {
    "NewGateway": "custom_payments"
  }
}
```

After placement the database is validated automatically by `validate_dork_database()` (Pydantic models + custom rules for engines, mappings, and non-empty lists). Failures raise clear `ValueError` messages at import time.

**Validation rules enforced**
- Every tech in `dorks` must map to a known category.
- Every pattern must be a dict containing a non-empty `signature` and a valid `engine` (`google`, `bing`, `duckduckgo`, `baidu`, or `yandex`).
- Categories must conform to the `Category` Pydantic model.
- The `generate_dorks_for_tech()` helper (or the `/api/generate_dorks` endpoint) can be used to bootstrap realistic patterns for new technologies.

Community contributions should follow this format, update the corresponding test cases in `tests/test_dorks.py`, and ensure the full test suite passes.

## Advanced / Code-Level Configuration (`config.py`)

For settings that are not exposed via environment variables, edit `src/dorkscout/config.py` directly:

- `SEARCH_ENGINES` – list of dicts defining base URLs, query parameters and rate limits for each search engine.
- `BIG_BRAND_KEYWORDS` – set of keywords used by `calculate_relevance_score()` to penalize large marketplaces.
- `BACKOFF_CONFIG` – controls exponential backoff + jitter used by `SearchClient`.
- `DEFAULT_DORK_DB` – the embedded production dork catalog (hundreds of patterns across 60+ technologies).
- `ALERT_CONFIG` – default SMTP and webhook payload settings (most fields can be overridden by the environment variables above).

`TECH_SIGNATURES` (180+ regex patterns) lives in `detector.py` and can be extended there. The browser extension bundles a copy of the same signatures.

## Persistence

The SQLite database location is controlled by `DORKSCOUT_DB_PATH`. The `DiscoveryStore` automatically creates and migrates the required tables (`domains`, `search_history`, `scheduled_jobs`). All searches, scheduled jobs, and discovered domains are persisted, enabling deduplication, analytics, and freshness detection for alerts.

## Best Practices

- Use a volume mount for `dorkscout.db` and `dorks.json` when running in Docker.
- Keep `dorks.json` under version control when adding new patterns so they can be reviewed.
- After any change to `dorks.json` run `pytest tests/test_dorks.py -k validate` to confirm the database remains valid.
- For production alert delivery, always configure the SMTP environment variables and test with a scheduled job.

Full source references:
- `src/dorkscout/config.py`
- `src/dorkscout/dorks.py` (loading, merging, validation, LLM helper)
- `src/dorkscout/models.py` (Pydantic schemas)
- `tests/test_dorks.py` (validation and extension tests)

See the [README.md](README.md) for usage examples and the [roadmap](README.md#roadmap) for upcoming configuration improvements (custom noise-domain blocklist, etc.).
