# Local credential store — NEVER COMMIT

This directory holds provider API tokens used by `deploy/provision-free.py`.
`.gitignore` excludes everything here except this README.

Create `tokens.env` here (see `deploy/tokens.env.example`) and restrict it:

    chmod 600 deploy/.secrets/tokens.env

Nothing in this directory is ever printed to the terminal by the provisioning
script; it masks every value it reads.
