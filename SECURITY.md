# Security

Report a vulnerability privately through GitHub's advisory form:
https://github.com/dunnolab/nethackers/security/advisories/new. Don't open a
public issue for it. We reply within a week.

In scope: the hub, the CLI, the sandbox images, and the verifier. What each
one contains and what it does not is written down in
[docs/safety.md](docs/safety.md); a report that shows one of its stated
limits being crossed in a way we didn't foresee is the most useful kind.

Out of scope: a bot influencing its own public score. That is a known limit
of the public tier, and the private tier exists because of it.
