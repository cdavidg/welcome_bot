# Changelog

All notable changes to this project will be documented in this file.

## [v1.0.0] - 2025-09-17
### Added
- Initial import of the welcome bot code.
- Unified welcome + CTA into a single combined message to improve mobile layout.
- Persistent scheduling for auto-delete of welcome messages using on-disk JSONL so jobs survive restarts.
- VS Code workspace settings and recommended extensions (Copilot, Python, Pylance, Black).
- Pre-commit configuration with `black`, `isort`, and `flake8`.
- `tools/git_autocommit.sh` helper to format, commit and push changes.
- `requirements.txt` with pinned dependencies.

### Fixed
- Handler ordering and duplicate handler issues causing welcome messages to be missed.

### Notes
- Do not store bot tokens or credentials in the repository; use environment variables or secrets.
