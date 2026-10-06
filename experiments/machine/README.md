# Machine experiment

**Questions.** When the machine runs a program, does each instruction decode to the same micro-op
every time? And how much of the decoding can memory take over?

## Method

- The program is `sys.health` (`plugins/sys.health/plugin.toml`): five plain-language instructions.
  Two read the machine through a command, two each judge one reading, and one writes a line.
- The machine runs the instructions in order. For each one the decode model returns a single
  micro-op: `call` a command and store its output in a register, `set` a register to a value it
  worked out, or `halt`.
- The decoder is shown the instruction and only the registers it names in backticks. A register
  named in braces is passed along unseen: the decoder writes the braces and the machine fills in
  what the register holds.
- **Live:** 20 runs, each reading the machine again. Every instruction goes to the model.
- **Frozen:** 20 runs that replay the first run's command outputs, so every run decodes exactly the
  same inputs. Every instruction goes to the model.
- **With memory:** 20 live runs starting from an empty memory. A decode whose whole input has been
  seen before is not sent to the model.
- Every cycle is saved in `results/`.

## Running it

```
scripts/serve.sh gemma-4-26B-A4B-it-qat-q4_0.gguf       # in another terminal
experiments/machine/stability.py sys.health --runs 20
experiments/machine/stability.py sys.health --report     # the report again, from the saved cycles
experiments/machine/step.py sys.health 5 mem_level=fine,worrying temp_level=fine,worrying
```

`stability.py` asks the server directly, with its prompt cache on, as the shell did when these
results were made. `step.py` asks the way the shell asks now (see `los.toml`).

## Results, 2026-10-06 (Gemma 4 26B-A4B)

In short:

- **Identical inputs gave identical micro-ops**, in all 100 frozen cycles. This held within one
  start of the model server, for runs that asked in the same order every time. It did not hold
  across a restart (see the last step, below), and the restart experiment later found that it
  does not hold across another order of questions either.
- **The two `call` steps are constants.** They name no register that holds anything, so their input
  never changes and neither does their micro-op.
- **A judgement follows its reading.** Memory was always judged fine. The temperature took 8 values
  in the live runs: it was judged fine at 72, 78 and 79 °C and worrying from 84 °C, every time.
- **The last step has two inputs, not one per reading.** It reads only the two levels and passes the
  readings along in braces, so it produced two lines in 20 live runs where the earlier version,
  which read the readings, produced seven.
- **Memory answered 94 of 100 cycles.** Time in the decoder fell from 8.6 seconds a run to 0.5 on
  average. The first run used the model for all five steps, the second for one, and the other 18
  for none.
- **A cycle sent to the model takes 1.3 to 2.1 seconds.** Most of that is writing the micro-op,
  which is about 43 tokens of JSON.

### Live: every run reads the machine again

| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |
|---|---|---|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 1 | 20/20 | 1 | 1.35 |
| 2 | Read the temperature and store it in `temp`. | 1 | 20/20 | 1 | 1.35 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 1 | 20/20 | 2 | 1.87 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 2 | 17/20 | 8 | 1.93 |
| 5 | Write one short line for the user. If `mem_level` and `temp_level` are both fine, the line is: The machine looks healthy. Otherwise the line starts with Worrying: and then lists {mem} if `mem_level` is worrying and {temp} if `temp_level` is worrying, separated by a semicolon. Store the line in `verdict`. | 2 | 17/20 | 2 | 2.10 |

- Step 3 stored "fine" (20)
- Step 4 stored "worrying" (17); "fine" (3)
- Step 5 stored "Worrying: {temp}" (17); "The machine looks healthy." (3)

### Frozen: every run replays the first run's command outputs

| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |
|---|---|---|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 1 | 20/20 | 1 | 1.35 |
| 2 | Read the temperature and store it in `temp`. | 1 | 20/20 | 1 | 1.34 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 1 | 20/20 | 1 | 1.88 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 1 | 20/20 | 1 | 1.93 |
| 5 | Write one short line for the user. If `mem_level` and `temp_level` are both fine, the line is: The machine looks healthy. Otherwise the line starts with Worrying: and then lists {mem} if `mem_level` is worrying and {temp} if `temp_level` is worrying, separated by a semicolon. Store the line in `verdict`. | 1 | 20/20 | 1 | 2.10 |

- Step 3 stored "fine" (20)
- Step 4 stored "worrying" (20)
- Step 5 stored "Worrying: {temp}" (20)

### With memory: every run reads the machine again, memory starts empty

| Step | Instruction | Runs answered from memory |
|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 19/20 |
| 2 | Read the temperature and store it in `temp`. | 19/20 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 19/20 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 18/20 |
| 5 | Write one short line for the user. If `mem_level` and `temp_level` are both fine, the line is: The machine looks healthy. Otherwise the line starts with Worrying: and then lists {mem} if `mem_level` is worrying and {temp} if `temp_level` is worrying, separated by a semicolon. Store the line in `verdict`. | 19/20 |

Memory answered 94 of 100 cycles. Time a run spends in the decoder: 8.6 s without memory (median); with it, 0.5 s on average and 0.0 s at the median.

### The last step, for every combination of levels

`step.py` decodes one step for each combination of register values. The last step reads two
registers with two values each, so it can be checked completely:

| mem_level | temp_level | Micro-op |
|---|---|---|
| fine | fine | set verdict = The machine looks healthy. |
| fine | worrying | set verdict = Worrying: {temp} |
| worrying | fine | set verdict = Worrying: {mem} |
| worrying | worrying | set verdict = Worrying: {mem}; {temp} |

All four are right. An earlier wording of this instruction got the second row wrong: it wrote
`{mem}` when only the temperature was worrying. It was just as repeatable as the correct one, and
the mistake only showed because the stored lines are listed in the report.

**The same step later decoded wrongly after the model server was restarted.** With the same model
file, the same instruction and the same input, the second row came back as the instruction's own
wording: `Worrying: {mem} if mem_level is worrying and {temp} if temp_level is worrying, separated
by a semicolon`. Memory then repeated it on every run. What differed between the two server starts
was not measured at the time. The guess was how the layers were split between RAM and GPU, which
llama.cpp decides from the GPU memory free at start, and another program was using the GPU the
second time. The [restart experiment](../restart/README.md) confirmed it: with 1,640 MiB of GPU
memory held by another program the same wrong micro-op came back, and with the split fixed it
did not.

The four cases are now pinned in the program: the micro-op for each is written in `plugin.toml`
and the decoder is never asked. A step that only arranges a few known values should not depend
on a model.

### A rule for the temperature judgement

`rule sys.health 4` in the shell handed the recorded answers for step 4 to Claude Opus 5.5 and
asked for a function that reproduces them. Eight different temperatures were on record. Six were
shown to the author and two were held back to test what it wrote.

```python
import re


def rule(registers):
    text = registers.get("temp") if isinstance(registers, dict) else None
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"Temperature: (\d+) °C at the hottest sensor \(acpitz\)", text.strip())
    if not m:
        return None
    t = int(m.group(1))
    if 20 <= t <= 78:
        return "fine"
    if 84 <= t <= 110:
        return "worrying"
    # 79-83 is not settled by the cases; outside 20-110 looks like a bad reading.
    return None
```

- **It answers 20 to 78 °C as fine and 84 to 110 °C as worrying.** It returns nothing for 79 to
  83 °C, for readings outside 20 to 110 °C, and for any other wording or sensor. The model is
  asked in those cases, as before.
- **It passed the test.** It gave the recorded answer for the six cases it was shown. Of the two
  held back, it answered one correctly and left the other, 79 °C, to the model.
- **The author also reviewed the answers.** It found them consistent, with the line somewhere
  between 78 and 84 °C, and called that sensible for a machine in use. This is the first check
  anything has made on whether the judgements are right, and it is one model's opinion of another's.
- **With the rule installed, a run needed no model call**: four steps from memory and one by rule.

The first attempt was refused, wrongly. The test then required the recorded answer for every
case, and the author had left 79 °C unanswered because nothing it was shown settled it. Declining
to answer is always safe, since the model is asked instead, so the test now refuses a rule only
for contradicting a held-back case or for missing one it was shown.

## What it means

- Decoding is repeatable enough to remember: nothing varied that did not have a reason to.
- Memory needs each instruction to read as little as possible. A step shown every register can
  rarely be remembered. A step shown only what it names, and passing the rest along in braces, has
  few possible inputs and is soon remembered for all of them.
- Readings come from a small set of values, so memory fills in a judgement one value at a time.
- A step with few possible inputs can be checked for every one of them, and should be whenever its
  wording changes.
- A judgement over a range of values can become a rule that covers values nobody has judged yet.
  The rule is only as right as the answers it was built from.

## Caveats

- One program, one model, one machine.
- Stable is not the same as right. Nobody checked whether the low 80s is the right place to start
  worrying on this laptop, and an earlier version of the decoder's prompt drew the line lower: it
  called 74 °C worrying.
- The memory result flatters steady conditions. The runs were back to back, and once the model
  stopped being called the machine settled at one temperature, so the readings repeated.
- The check heats the machine it is checking: while every step went to the model the temperature
  sat at 84 to 88 °C.
- A remembered decode is as old as the server start it was made under. A restart changed one
  answer, and memory does not notice a restart. Since the restart experiment the split is fixed
  and the server is asked with its prompt cache off, so a restart no longer changes answers on
  this machine. Memory still does not notice another machine or another llama.cpp build.
- The rule was built from answers recorded under one server start.
