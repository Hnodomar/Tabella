# Project context
This project implements a to-do app that runs natively on Ubuntu Desktop.

# Engineering conventions
- Ask for approval with a short summary of motivation when adding third party libraries.
- Follow existing repository tooling and structure, asking for approval if any change to this is desired.
- Keep different concerns separate in code.
- Always write unit tests against new or changed APIs, creating mocks where appropriate.

## Git workflow

- Check the current branch with "git branch --show-current".
- Never commit to the master git branch.
- Always checkout a separate git branch named "codex/<descriptive_name>" before making changes.
- Always create a new worktree for the current session such that other agents making changes do not interfere with your work.
- All branches created must use the "codex/" prefix.
- Always complete one coherent, working change and verify it before committing it with git according to Conventional Commit v1.0.0 guidelines https://www.conventionalcommits.org/en/v1.0.0/.
- Always add a concise summary of what changed, why it changed and what the effect is (if there is any) to each secondary commit message via a second "-m" argument in "git commit".
- After committing a verified change, push its `codex/` branch and open a pull request targeting `master` for review. Do not merge the pull request yourself. Include the change summary and verification results in the pull request description.
- Before every commit, run the relevant tests, linting and type checking.
- Never include pre-existing user changes in commits.

# Verification

- Run relevant tests, linting and type checking after implementation.
- If a test fails to pass, always ask for approval before editing it with a brief description of why the test needs to change.
- Report the commands run and their outcomes.
- Clearly identify anything that could not be verified.
