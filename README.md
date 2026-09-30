# random

A home for small experiments and one-off projects. Put each new project in its own directory and commit it to this repository instead of creating a new GitHub repo.

## Projects

- [`dbwatcher/`](dbwatcher/) — simple SQLite database viewer
- [`phrasedeck/`](phrasedeck/) — self-hosted spaced-repetition flashcards
- [`family-pool/`](https://github.com/WahidinAji/family-pool) — separate repository, included as a Git submodule
- [`my-profile/`](https://github.com/WahidinAji/my-profile) — separate repository, included as a Git submodule

Clone with `git clone --recurse-submodules https://github.com/WahidinAji/random.git` (or run `git submodule update --init --recursive` after cloning).

For a new experiment, create a directory here and run `git add <directory> && git commit && git push` from the **random** root. Keep databases, secrets, and generated files out of Git; add project-specific exclusions to `.gitignore` as needed. If a project already has its own repository and remote, add it as a submodule instead (`git submodule add <repo-url> <directory>`). Submodules have their own commits and pushes: push changes inside the project first, then commit the updated submodule pointer here.
