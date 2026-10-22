# ADR 0007 - Isolation boundaries

**Status:** accepted

## Context

An agent runner executes real tools and shell commands.  The requirement is
that a test agent must not be able to reach production credentials, write
outside its workspace, or damage the machine it runs on.  There is a tempting
confusion here: *"the prompt tells the agent not to read secrets"* is not
access control.  Only the operating system can enforce that.

## Decision

Three distinct layers, each with its own mechanism:

| Layer | Mechanism | Where |
|---|---|---|
| Credentials | the subprocess environment is built from scratch and the credential variables are removed | `isolation/subprocess.py::build_env` |
| Filesystem | every run gets a fresh workspace; the manager refuses to delete anything outside its own root | `isolation/workspace.py` |
| Privileges | dangerous permission modes are refused unless explicitly enabled | `runners/claude.py` |

Concretely:

* `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AWS_SECRET_ACCESS_KEY`,
  `GITHUB_TOKEN`, ... are stripped from the environment handed to a real
  runner.  The agent therefore cannot read them **even if it tries**.
* `--inherit-env` is the explicit opt-out for a local, trusted run.  The CLI
  prints a warning when it is used.
* `--dangerously-skip-permissions` / `bypassPermissions` are refused by the
  Claude Code adapter unless `allow_bypass_permissions` is set explicitly.
* Workspace deletion requires a sentinel file, so an unrelated directory under
  the workspaces root is never removed.
* Secrets that do reach the artefacts (an agent printing one, for instance)
  are redacted on write, and the report records how many redactions happened.

## Consequences

* On a machine where the runtime authenticates through an environment
  variable, a real run fails with a clear `api_error` until the operator
  passes the credential explicitly.  That failure is reported as a run
  failure with the diagnostic, never as a successful run with invented
  numbers.
* The distinction between "the prompt asked the agent not to" and "the OS
  prevented it" is preserved in the code and in the documentation, because
  only the second one is a guarantee.
