# Batch Paper Download — Multi-Auth Design (VPN + CARSI)

**Date:** 2026-07-30
**Status:** approved
**Scope:** `batch-wos-download.py` + `config.yaml` + `SKILL.md`

## Goal

Make `batch-wos-download.py` shareable across schools. Users configure their own institution (VPN or CARSI) via a single `config.yaml`, and the script works without code changes.

## Architecture

```
batch-paper-download/
├── batch-wos-download.py    # Main script (reads config, no hardcoded school)
├── pdf_server.py            # Unchanged
├── config.yaml              # [NEW] User-editable config
├── SKILL.md                 # Agent-facing skill doc (installable)
└── papers_to_download.txt   # Sample input
```

## Config Schema (`config.yaml`)

```yaml
school:
  name: "合肥工业大学"
  english_name: "Hefei University of Technology"

auth:
  method: "vpn"          # vpn | carsi

  vpn:
    url: "https://webvpn.hfut.edu.cn/"
    login_link: "CAS"
    timeout: 300

  carsi:
    timeout: 300
    probe: ""          # optional: subscriber-only article URL to verify access after login

download:
  output_dir: "./downloads"
  skip_existing: true
  delay: 2             # optional: seconds between papers (anti-throttling)
```

- `auth.method` chooses the path
- VPN mode needs `vpn.url` and `vpn.login_link`
- CARSI mode reuses `school.name` / `school.english_name` for institution search

## Auth Flows

### VPN (existing, generalized)

```
VPN home → click login_link → user logs in → poll URL → done
```

- URL and link text come from config
- Publisher URL prefix encoding still auto-detected from VPN home page
- `_KNOWN_PREFIXES` moved to config (optional, fast-path for known VPNs)

### CARSI (new)

```
Publisher home → click "Sign in" → "Institutional login"
→ type school name → select institution
→ redirect to school IdP → user logs in → poll URL → back on publisher → cookies set
→ direct download from publisher (no URL encoding, no VPN)
```

Per-publisher automation steps stored in `PUBLISHERS[pk]["carsi_login"]`:
```python
"carsi_login": {
    "entry_url": "https://www.sciencedirect.com/",
    "steps": [
        {"click_any": ["Sign in", "Log in"]},  # multiple UI text candidates
        {"type": "{school_name}", "into": "search"},
        {"click": "{school_name}"},
    ],
    "success": {
        "url_contains": "sciencedirect.com",
        "url_not_contains": ["/login", "/shibboleth", "idp."],
    },
}
```

- First visit per publisher per session: full CARSI flow
- Subsequent visits: cookies reuse, skip auth
- Fallback: if selector fails, prompt user to manually complete institution login
- Login success requires evidence: URL must have left the entry page, or an
  auth hop (IdP/wayf) must have been observed — prevents false positives when
  the entry URL is the publisher home page (e.g. ACS)
- On wait timeout the script re-loads the entry page once and re-checks,
  covering the case where the user completed IdP login in another tab
- If `auth.carsi.probe` is set, the script opens that article page after login
  and warns when paywall markers are detected; the real PDF download remains
  the final arbiter
- `BPD_CONFIG` env var overrides the config path (test fixtures, multi-school
  switching, CI)

## Code Changes Summary

| Area | Change |
|------|--------|
| Imports | Add `yaml` |
| Constants | Remove `VPN_HOME`; add `config` global loaded from `config.yaml` |
| `ensure_vpn()` | Read params from `config` instead of constants |
| `ensure_auth()` | **NEW** — dispatches to `ensure_vpn` or `ensure_carsi` based on `config.auth.method` |
| `ensure_carsi()` | **NEW** — session-level CARSI entry point, tracks which publishers are already authed |
| `carsi_authenticate(sid, pub_key)` | **NEW** — walks `carsi_login.steps`, polls for login completion |
| `get_vpn_prefix()` → `get_access_url()` | **RENAME** — VPN mode returns encoded prefix, CARSI mode returns native domain |
| `download_pdf()` | Unchanged logic; CARSI passes native publisher URLs |
| `process_one()` | Calls `get_access_url` instead of `get_vpn_prefix` |

## Edge Cases

| Scenario | Handling |
|----------|----------|
| `config.yaml` missing | Generate template, print instructions, exit |
| `config.yaml` parse error | Catch exception, report line |
| CARSI selector not found | Fallback: navigate to publisher, prompt manual login |
| CARSI login timeout | Same as VPN: report timeout, suggest checking IdP |
| CARSI login in another tab | Re-load entry page after timeout and re-check URL/probe |
| Paywall still shown after login | `auth.carsi.probe` warning; download failure prints CARSI hint |
| Publisher lacks `carsi_login` | Error: "publisher not supported in CARSI mode, use VPN" |
| Multiple school IdPs | Default: search by `school.name`; advanced: set `carsi.idp_entity_id` in config |

## Unchanged

- `pdf_server.py` — now requires `PDF_SERVER_TOKEN` to start (security default)
- Input parsing, search logic, chunked base64 transfer, PDF magic validation — all unchanged
- `BROWSER` constant — still in code (or optionally moved to config)

## Dependencies

- **New:** `pyyaml` (`pip install pyyaml`)
- Existing: `bsk` CLI, Chrome + browser-skill extension

## Estimated Size

~150-200 lines of new/changed code across the script, ~50 lines for `config.yaml` template.
