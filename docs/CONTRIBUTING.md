# Contributing to EndpointClaw

Thanks for your interest in contributing! Here's how to get involved.

## Getting Started

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests: `pytest tests/` (or equivalent)
5. Commit with clear messages: `git commit -m "feat: add file type classification"`
6. Push and open a PR

## Code Style

- Python: Follow PEP 8, use type hints
- C#: Follow Microsoft naming conventions
- TypeScript/React: ESLint + Prettier
- Keep functions small and focused
- Write docstrings/comments for non-obvious logic

## What to Work On

Check the [Issues](https://github.com/dwflickinger/endpointclaw/issues) tab for open tasks. Good first issues are labeled `good-first-issue`.

### Priority Areas

- **Phase 1A Foundation** — Windows service, file indexing, local chat, relay protocol
- **Tests** — Unit and integration test coverage
- **Documentation** — Architecture docs, setup guides, API docs

## Playbooks

The playbook **engine** is open source. Company-specific playbook **definitions** (which encode proprietary workflows and business logic) are kept in private repositories and are not part of this project.

## Pull Request Guidelines

- One feature/fix per PR
- Include tests for new functionality
- Update docs if behavior changes
- Keep PRs focused and reviewable (< 500 lines when possible)

## Reporting Issues

- Use the issue template
- Include OS version, agent version, and steps to reproduce
- Logs from `%APPDATA%\EndpointClaw\logs\` are helpful

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
