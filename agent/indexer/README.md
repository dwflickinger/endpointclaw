# Indexer

File indexing engine with real-time filesystem monitoring.

- Initial full scan on first run
- Continuous monitoring via `ReadDirectoryChangesW`
- Daily reconciliation scan
- Content extraction and classification
- SQLite + FTS5 for full-text search
