# Contributing to AusTenancy.ai

Thank you for contributing to the Australian Residential Tenancies Compliance Agent. This document defines the team development governance.

## Git Branching Strategy

We follow a **Git Flow** model:

| Branch Type      | Naming Convention                         | Source        | Merge Target |
| ---------------- | ----------------------------------------- | ------------- | ------------ |
| Main             | `main`                                    | —             | —            |
<!-- | Develop          | `develop`                                 | —             | `main`       | -->
| Feature          | `feature/<issue-number>-<short-desc>`     | `main`     | `main`    |
| Bug Fix          | `fix/<issue-number>-<short-desc>`         | `main`     | `main`    |
| Hotfix           | `hotfix/<issue-number>-<short-desc>`      | `main`        | `main`       |
| Release          | `release/<version>`                       | `main`     | `main`       |
| Documentation    | `docs/<issue-number>-<short-desc>`        | `main`     | `main`    |
| Chore            | `chore/<issue-number>-<short-desc>`       | `main`     | `main`    |

### Workflow

1. Create a feature/fix branch from `main`.
2. Commit using the conventional commit format (see below).
3. Open a pull request targeting `main`.
4. Ensure all CI checks pass (lint, type-check, tests).
5. Squash-merge into `main`.
<!-- 6. `develop` is periodically merged into `main` via release branches. -->

## Conventional Commits

All commit messages **must** follow the **Angular convention**:

```
<type>(<scope>): <short summary>

<body (optional)>

<footer (optional)>
```

### Allowed Types

| Type     | Usage                                    |
| -------- | ---------------------------------------- |
| `feat`   | A new feature                            |
| `fix`    | A bug fix                                |
| `docs`   | Documentation only changes               |
| `chore`  | Build, CI, or tooling changes            |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test`   | Adding or correcting tests              |
| `style`  | Formatting, missing semicolons, etc.     |
| `perf`   | Performance improvement                  |

### Examples

```
feat(ingest): add hierarchical Act-Part-Section chunking
fix(retrieval): correct metadata filter for NSW jurisdiction
docs(readme): add Qdrant setup instructions
chore(deps): upgrade langchain to 0.3.0
```

## Linting & Formatting

We use **Ruff** as the unified Python linter and formatter (replacing Black + Flake8 + isort).

### Setup

Ruff is configured in `pyproject.toml`. Run the following commands:

```bash
# Lint all files
ruff check .

# Auto-fix issues
ruff check --fix .

# Format all files
ruff format .
```

### Pre-commit Hook

Install the pre-commit hook to enforce linting before every commit:

```bash
pip install pre-commit
pre-commit install
```

## Pull Request Process

1. Ensure your branch is up to date with `develop`.
2. Run `ruff check .` and `ruff format .` — no warnings allowed.
3. Run the test suite: `pytest tests/`.
4. Update documentation if your changes affect the public API or configuration.
5. Request review from at least one maintainer.
6. Squash-merge into `develop` after approval.

## Code of Conduct

Be respectful, constructive, and inclusive. We enforce a strict no-tolerance policy for harassment or discrimination.
