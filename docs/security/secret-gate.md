# Secret gate

The secret gate stops text that looks like a secret before it leaves your computer for a model that is
not local or for an executor application. The send waits until you approve that exact text with one
click in the GUI. It is always on: no setting turns it off, and it does not depend on
`EGRESS_DEFAULT` or on a step's `egress:` policy.

It is a safety net for mistakes, such as a key pasted into a message or a private key inside a file a
step reads. It is not a vault and not a sandbox. Read [What the gate does not see](#what-the-gate-does-not-see)
and [The risk that remains](#the-risk-that-remains) before you rely on it.

## What is stopped

The gate reads the text that is about to be sent and looks for four kinds of secret:

| Type | What it looks for |
| --- | --- |
| `private_key` | A PEM private key block. |
| `secret_key` | Known key formats (OpenAI-style, Google API, AWS, GitHub, Slack and similar) and assignments such as `password=...`, `api_key: ...` or `client_secret=...`. |
| `bearer` | `Bearer` tokens, `Authorization: Basic ...` values and three-part JSON web tokens. |
| `long_token` | A long run of base64-like characters with high entropy, outside URLs, UUIDs, hex digests and blob references. |

Personal data (emails, phone numbers, documents, financial numbers) is not a secret type here. The egress
policy handles it (`EGRESS_DEFAULT`, the `egress:` policy of a step).

The gate applies to two kinds of destination:

- **Model calls to a model that is not local.** Every call checked by the call guards of a run, the
  model planner, and the free-text reader of «Nueva sesión» pass through it. Models whose catalog tier is
  `local` (Ollama) are not gated, because the text never leaves your computer.
- **Executors.** Before every launch and every resume of a task with Claude, Codex, Copilot or agy, the
  gate reads what the framework would send: the rendered prompt and the task's objective, constraints,
  context references and file scope.

If a step's egress policy is `block` or `redact`, the egress guard handles the text that carries a secret
(it refuses or redacts it) and the gate lets that call go on to it. With any other policy, the gate asks
you.

## Only one click in the GUI approves

A held send is approved in the GUI view «Aprobaciones» (`#approvals`), and nowhere else. The command line
(`multiagent approve`), the HTTP API (`POST /api/v1/runs/{id}/approve`, the task approval route), MCP and
the GUI's own token-only routes all answer `user_click_required` for a secret. Rejecting a held run still
works from any channel, because rejecting sends nothing.

The reason is that every one of those channels can be driven by a program. An agent that holds the API
token or can run the CLI could approve its own send. The browser click is the one step that asks for a
person at the keyboard.

### The startup code and the browser session

- `multiagent gui` creates a random startup code for each start. It is passed to the browser in the
  fragment of the URL (`#token=...&approval=...`) and never sent to the server by the browser's normal
  request. With `--no-browser` the command prints the full URL with the code so you can open it yourself.
  Without that option the URL printed on the console has no token and no code; the browser opens the full
  one.
- The page exchanges the code once for a cookie (`HttpOnly`, `SameSite=Strict`, limited to
  `/gui/api/approvals`). The code then stops working. Only one browser takes the approvals for each start of
  the GUI; a second tab or browser, or a reload after the code was used, shows
  «Otra pestaña o navegador ya tomó las aprobaciones: reinicia la GUI». Restarting the GUI creates a new
  code and drops the old cookie and any open challenge.
- A decision needs the GUI token, the cookie and one more thing: a challenge code made for that approval.
  `POST /gui/api/approvals/{id}/challenge` returns a one-use code tied to the approval, its SHA-256 and the
  browser session. It expires after 120 seconds. The decision sends the code with `approve` or `reject`
  in the same click. A wrong, reused or expired code, or a code for another approval, is refused
  with `user_click_required`.
- There is no «remember» or «approve all». Each approval is for one exact text and one destination.

## What happens after the click

- **Runs.** A run that reaches a secret stops in `waiting_human` with the reason `secret_review`. Approving
  puts it back in the queue and the one call goes out; the approval is spent when the call goes. Rejecting
  fails the run (`Secret review rejected`) and the provider is never called. A different text, even a retry
  with other words, asks again.
- **«Nueva sesión» and the planner.** The request is not sent and the answer says
  `secret_review_required` with an `approval_id`. The message stays in the box. After you decide, send it
  again and it goes with the `approval_id`.
- **Executor tasks.** The task is held on an approval condition. When you click, the GUI checks the held
  task at once. If the approval is valid and the text is the same one, it launches once and spends the
  approval. If you reject, the task does not launch. Automatic resumes, the worker and hand-offs never
  approve on their own: they find the same hold and wait.

## What is stored

Only these things are stored, in events of the team and of the run:

- the secret type or types found (`private_key`, `secret_key`, `bearer`, `long_token`);
- a masked sample of at most three findings, with three characters at each end of a long value and `***`
  for a short one;
- the SHA-256 digest of the text, which is what an approval is bound to, and the destination (model id
  and tier, or executor and model).

The value of the secret is never stored, logged, shown in the approvals card or put in an event. For
executors, the digest also covers the values found, so a different secret in the same text is another
request. The events are listed in [Telemetry](../TELEMETRY.md#secret-gate-events).

## What the gate does not see

- **It reads text that the framework sends.** An executor application can read files on disk by itself.
  A key in a file the executor opens is not in the prompt the framework gave it, and the gate cannot see
  it. The permission templates in [`docs/permissions/`](../permissions/) (read-only by default, deny rules
  for key and credential files) remain the defense against that. Keep them in use.
- **It matches patterns.** A secret with a shape it does not know, an obfuscated value or one split across
  messages can pass. A harmless long string can be held; reject it or approve it.
- **It does not read what a model returns**, files a model reads through tools, or your own commands in a
  terminal.
- **It does not gate local models.** Ollama stays on your computer.

## The risk that remains

On a PC where the agents run as the same Windows user as you, the approval is a strong guard against
mistakes and against a program that calls the framework, but not a barrier against a determined program.
A process of the same user can read the GUI token file, read the browser's memory or cookies, send input
to the browser window, or drive the browser itself. The cookie, the one-use challenge and the startup code
make that harder and make a careless automatic approval impossible, but they do not stop a program that
is built to impersonate your click. If you do not trust a program that runs as you, do not leave it running
while you use the GUI, and reject an approval you did not expect.

A real boundary against automation would need a check that a program of the same user cannot answer, such
as Windows Hello through WebAuthn. That needs the server to verify signatures, which means a new
dependency or home-made cryptography, and it is not part of this release.

## Your first real approval

The tests in this repository approve with a test client and seeded values. No agent, worker or test
presses «Aprobar» in the real GUI. The first real «Aprobar» is yours: open the GUI with `multiagent gui`,
send or launch something that carries a secret you can discard (a fake key), and decide it yourself.

## Related

- [Operator GUI: Approvals](../GUI.md#approvals)
- [HTTP API: approvals and `user_click_required`](../API.md#approvals-and-user_click_required)
- [Executor applications: the secret gate](../EXECUTORS.md#the-secret-gate-before-a-launch)
- [Model routing: the chain of guards](../model-routing.md#the-chain-of-checks-before-a-call)
- [Permission profiles](../PERMISSIONS.md)
