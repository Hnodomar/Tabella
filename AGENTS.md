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
- Create a new worktree and `codex/` branch for each individual change, including when making multiple changes in one session, so concurrent work does not interfere.
- All branches created must use the "codex/" prefix.
- Always complete one coherent, working change and verify it before committing it with git according to Conventional Commit v1.0.0 guidelines https://www.conventionalcommits.org/en/v1.0.0/.
- Always add a concise summary of what changed, why it changed and what the effect is (if there is any) to each secondary commit message via a second "-m" argument in "git commit".
- Before raising a pull request, run `gh auth status`. If GitHub CLI is not authenticated in this environment, ask the user to run `gh auth login --web --git-protocol ssh`; never request or record their token.
- After committing a verified change, run `git push -u origin HEAD` from its worktree, then use `gh pr create --base master` to open a pull request. Provide a title and description summarizing the change, why it was made, its effect, and the verification commands and results. Do not merge the pull request yourself.
- Once a change's pull request has been merged, remove its worktree with `git worktree remove`. Preserve and report any uncommitted changes that prevent removal.
- Before every commit, run the relevant tests, linting and type checking.
- Never include pre-existing user changes in commits.

# Verification

- Run relevant tests, linting and type checking after implementation.
- If a test fails to pass, always ask for approval before editing it with a brief description of why the test needs to change.
- Report the commands run and their outcomes.
- Clearly identify anything that could not be verified.
- When changing UI elements or behavior, end the final response with one line telling the user how to launch the app (for example, `make run`), what to do, and what result to expect.
