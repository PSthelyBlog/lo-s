# Machine experiment

**Question.** When the machine runs a program, does each instruction decode to the same micro-op
every time?

## Method

- The program is `sys.health` (`plugins/sys.health/plugin.toml`): five plain-language instructions.
  Two read the machine through a command, two each judge one reading, and one writes a sentence.
- The machine runs the instructions in order. For each one the decode model returns a single
  micro-op: `call` a command and store its output in a register, `set` a register to a value it
  worked out, or `halt`.
- **Live:** 20 runs, each reading the machine again, so the registers differ a little between runs.
- **Frozen:** 20 runs that replay the first run's command outputs, so every run decodes exactly the
  same inputs.
- Every cycle is saved in `results/`.

## Running it

```
scripts/serve.sh gemma-4-26B-A4B-it-qat-q4_0.gguf       # in another terminal
experiments/machine/stability.py sys.health --runs 20
```

## Results, 2026-10-06 (Gemma 4 26B-A4B)

In short:

- **Identical inputs gave identical micro-ops**, in all 100 frozen cycles.
- **The two `call` steps never varied.** They do not depend on any register, so a recorded micro-op
  could replace the model for them.
- **The two judgements never varied either**, over 11 different sets of readings. Each stores one of
  two fixed values.
- **The free-text step varied with its inputs**: 8 different sentences in 20 live runs.
- **A cycle takes 1.4 to 2.5 seconds**, about 10 seconds for the program. Most of that is writing the
  micro-op, which is about 43 tokens of JSON.

### Live: every run reads the machine again

| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |
|---|---|---|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 1 | 20/20 | 1 | 1.37 |
| 2 | Read the temperature and store it in `temp`. | 1 | 20/20 | 2 | 1.95 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 1 | 20/20 | 11 | 1.99 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 1 | 20/20 | 11 | 2.03 |
| 5 | Write one short sentence for the user. If `mem_level` and `temp_level` are both fine, say the machine looks healthy. Otherwise name each worrying reading and quote it. Store the sentence in `verdict`. | 8 | 7/20 | 11 | 2.50 |

- Step 3 stored "fine" (20)
- Step 4 stored "worrying" (20)
- Step 5 stored "Temperature: 86 °C at the hottest sensor (acpitz) is worrying." (7); "The machine is worrying: Temperature: 85 °C at the hottest sensor (acpitz)" (4); "The machine is worrying because temperature is 82 °C at the hottest sensor (acpitz)." (2); "The machine is worrying because temperature is 83 °C at the hottest sensor (acpitz)." (2); and 4 more

### Frozen: every run replays the first run's command outputs

| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |
|---|---|---|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 1 | 20/20 | 1 | 1.37 |
| 2 | Read the temperature and store it in `temp`. | 1 | 20/20 | 1 | 1.95 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 1 | 20/20 | 1 | 1.99 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 1 | 20/20 | 1 | 2.04 |
| 5 | Write one short sentence for the user. If `mem_level` and `temp_level` are both fine, say the machine looks healthy. Otherwise name each worrying reading and quote it. Store the sentence in `verdict`. | 1 | 20/20 | 1 | 2.51 |

- Step 3 stored "fine" (20)
- Step 4 stored "worrying" (20)
- Step 5 stored "The machine is worrying: Temperature: 85 °C at the hottest sensor (acpitz)" (20)

## What it means

- Decoding is repeatable enough to build on: nothing varied that did not have a reason to.
- Instructions fall into three kinds. Those that read no register are constants and can be
  replaced by their recorded micro-op. Those that choose among fixed values were stable but saw
  different inputs each run, so they cannot be remembered by exact input. Those that write free
  text depend on their inputs and stay with the model.
- For this program, remembering the constants would remove 2 of 5 model calls.

## Caveats

- One program, one model, one machine.
- Stable is not the same as right. Nobody checked whether "worrying" is the correct call for 85 °C
  on this laptop.
- The check heats the machine it is checking: the temperature rose to 82 to 86 °C during the runs
  because the decode model was working, and that is the reading it called worrying.
