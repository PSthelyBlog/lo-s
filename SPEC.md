# lo-s

A command-line operating system that runs on an emulated machine whose processor is a language
model. The core is small; everything else is a plugin, and the system grows a new plugin when the
user needs something it cannot do yet. It runs on Linux as an ordinary program.

Each item below is marked **Decided** (chosen by the project owner), **Proposed** (suggested during
design, not yet confirmed) or **Open**.

## The machine

- **Proposed.** A fetch, decode, execute loop. An instruction is a sentence. A model decodes it into
  one structured micro-op (`set`, `call`, `halt`), and deterministic code applies the micro-op to
  registers and context.
- **Proposed.** The emulator owns the program counter. A model never chooses the next instruction.
- **Proposed.** Tools are syscalls. A syscall is the only way to affect the host.

## The shell

- **Decided.** The user types structured commands, with plain language as the fallback.
- **Proposed.** A command is named `plugin.verb` and takes named parameters, typed as
  `plugin.verb --param value`.
- **Proposed.** Dispatch has three steps:
  1. The line parses as a known command: run it. No model is called.
  2. Otherwise the local model maps it to a known command, and the shell shows the structured form
     and asks before running it. Enter accepts a command that only reads; one that changes anything
     needs an explicit yes.
  3. Nothing fits: the need goes into a queue and the user is told.

## Plugins

- **Decided.** The core is extended through plugins, on the go, as needs arise.
- **Decided.** A new need is queued for the teacher model. The queue also drains when online.
- **Proposed.** A plugin bundles commands, syscalls, a permission list, recorded traces with checks,
  and the compiled rules it accumulates. It is the unit that is installed, trusted, optimized and
  removed.
- **Proposed.** On disk a plugin is a directory with `plugin.toml`, which declares its commands,
  their parameters and each command's effect (read, write or destructive), and `commands.py`.
- **Proposed.** A command written by a model lives in a plugin directory of its own, named
  `plugin.verb`, whose manifest records the need it answers and which model wrote it. Removing it
  is one deletion.
- **Proposed.** Before the user is asked, the proposed code is read without being run. It must
  define exactly the declared function, run nothing when imported, import only the standard
  library, and use no dynamic code. The user is shown what its imports let it do.
- **Proposed.** After installing, a new command must pass an acceptance check or it is removed:
  lines the user accepted before still reach the same commands, and the need's own line reaches
  the new one.
- **Proposed.** A command moves through three stages: written by the teacher, decoded by the local
  model, then compiled to plain code once its decodes stop varying.

## Models

- **Decided.** Any provider or model can fill any role. The owner's own setup uses Claude Opus 5.5
  as the teacher, through their own `claude` CLI login.
- **Decided.** The student is a mixture-of-experts model held in system RAM with the GPU doing the
  work that runs on every token.
- **Proposed.** Five roles, each assigned to a provider in configuration: `dispatch`, `decode`,
  `label`, `author`, `propose`.
- **Proposed.** One contract for all providers: a prompt and a JSON schema go in, JSON that validates
  comes out. The core validates and retries. See `los/models.py`.
- **Proposed.** Claude is reached only by running the user's own unmodified `claude` program. lo-s
  never reads, stores or sends that login.
- **Proposed.** Every label records its provider, model and date, and is asked for once, then
  replayed.

## Self-optimization

- **Decided.** The system searches for optimization opportunities in any aspect of itself.
- **Proposed.** Four things are outside the optimizer's reach: recorded traces, teacher labels, the
  acceptance test and the metric.
- **Proposed.** The loop: profile, let the `propose` role suggest a change, replay traces the proposal
  never saw, accept only if no labelled decode changes and the metric improves. Each accepted
  change is a commit.
- **Proposed.** The metric is wall-clock time per completed command, with zero regressions required.
- **Proposed.** The user is a judge: re-running, correcting or undoing a command counts as a label.
- **Proposed.** Three verdicts are recorded for a choice the model made. Accepted settles the line.
  Declined says nothing, because the choice may be right and simply unwanted. Wrong, given by
  queuing the line as a need or by typing `wrong`, unsettles it.
- **Proposed.** A settled line is answered from memory for as long as the command table has the
  version it was settled against. When a command is added, the acceptance check replays every
  settled line, and those lines are carried to the new version.

## Safety

- **Proposed.** Permissions are deny-by-default per plugin, and the user approves a new plugin's
  permissions before it is installed.
- **Proposed.** Destructive syscalls ask for confirmation or run as a dry run first.
- **Proposed.** The optimizer can never widen a permission. Acceptance stays in the core, even if
  authoring becomes a plugin.

## Built so far

- The shell and its three dispatch steps (`los/shell.py`, started with `./lo-s`). The user's answer
  to each choice the model makes is recorded as a label, with the version of the command table it
  was made against. Lines nothing fits are queued in `state/needs.jsonl`.
- Providers and role configuration (`los/models.py`, `los.toml`). The `dispatch` and `author` roles
  are used.
- `teach NUMBER` (`los/teach.py`): the author model writes a command for a queued need, the user
  approves it, and the acceptance check decides whether it stays. `forget NUMBER` drops a need.
- Three starter plugins, `fs`, `note` and `sys`, seven commands in all, written as plain code so
  that dispatch has something to run. They are not a decision about the day-one core.
- Memory (`los/memory.py`): the first optimization. A settled line skips the model, and `stats`
  reports how many lines memory answered and the model time that saved.
- Not built: the fetch, decode, execute loop; draining the queue without being asked; a sandbox
  or enforced permissions for written commands; tests of what a written command does; one commit
  per install; any optimization beyond memory; a search for optimizations.

## Open

- Which student model. Gemma 4 26B-A4B passed the first test; nothing else of its size was tried.
- Whether lo-s will be distributed to other people, which decides how strict the credential and
  permission rules must be.
- The micro-op set beyond `set`, `call` and `halt`.
- What the core contains on day one.

## Experiments

- `experiments/dispatch/`: does the local model pick the same command as the teacher as the command
  table grows from 10 to 200 entries? Run on 2026-10-05; results are in its README. What it means
  for this spec:
  - A 26B mixture-of-experts student dispatches well enough to build on (91% to 95%, flat as the
    table grows). An 8B one does not (57% at 200 commands).
  - The RAM and GPU split works on an 8 GB GPU: 32 to 40 tokens per second for a 14.4 GB model.
  - A label is only valid for the command table it was made against. Adding a plugin makes nearby
    labels stale, so labels need to record the table version as well as the provider and date.
  - Showing the structured form before running it is necessary, not optional: both students
    sometimes chose a destructive command the teacher would not have run.
  - Changing where weights sit changed the small model's answers on up to 16% of lines, so
    placement has to pass the acceptance test like any other optimization.
