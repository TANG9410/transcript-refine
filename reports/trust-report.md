# Security Trust Report

- OK: `True`
- Scanned files: `43`
- Scripts: `8`
- Internal script modules: `0`
- Secret findings: `0`
- Network-capable scripts: `2`
- Network policy covered scripts: `2`
- Network policy missing scripts: `0`
- File-write scripts: `2`
- Permission approvals: `2 / 2`
- Permission approval gaps: `0`
- CLI help smoke checked: `3`
- CLI help smoke failures: `0`
- Interactive scripts: `0`
- Package hash scope: `source-contract-without-generated-reports`
- Package hash files: `43`
- Package SHA256: `8981cadabc952806ec7c947b9cc019c93da01a22522771cad8ee90ed743335f0`

## Failures

- None

## Warnings

- No dependency or lock file detected
- CLI scripts without argparse/help surface: scripts/note_io_common.py, scripts/run_regression.py, scripts/test_optional_getnote.py, scripts/test_save_scripts.py, scripts/validate_exchange_transcript.py

## Dependency Evidence

- Files: `none`
- Pinned entries: `0`
- Unpinned entries: `0`

## Network Policy

- Policy file: `security/network_policy.json`
- Present: `True`
- Covered scripts: `2`
- Missing scripts: `none`
- Mismatches: `0`

## Permission Governance

- Policy file: `security/permission_policy.json`
- Present: `True`
- Required capabilities: `file_write, network`
- Approved capabilities: `file_write, network`
- Missing approvals: `none`
- Invalid approvals: `none`
- Expired approvals: `none`

## CLI Help Smoke

- Enabled: `True`
- Timeout seconds: `5.0`
- Checked scripts: `3`
- Passed scripts: `3`
- Failed scripts: `none`

## Script Surface

| Script | Interface | Declared | Argparse | Main Guard | Input | Network | File Write | Subprocess | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| scripts/note_io_common.py | cli | False | False | False | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/refine-transcript.py | cli | False | True | True | False | True | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/run_regression.py | cli | False | False | True | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/test_optional_getnote.py | cli | False | False | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/test_save_scripts.py | cli | False | False | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/update-note.py | cli | False | True | True | False | True | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/validate_exchange_transcript.py | cli | False | False | True | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/validate_transcript_artifact.py | cli | False | True | True | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
