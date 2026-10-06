# Machine experiment

**Questions.** When the machine runs a program, does each instruction decode to the same micro-op
every time? And how much of the decoding can memory take over?

## Method

- The program is `sys.health` (`plugins/sys.health/plugin.toml`): five plain-language instructions.
  Two read the machine through a command, two each judge one reading, and one writes a sentence.
- The machine runs the instructions in order. For each one the decode model returns a single
  micro-op: `call` a command and store its output in a register, `set` a register to a value it
  worked out, or `halt`. The decoder is shown the instruction and only the registers it names.
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
```

## Results, 2026-10-06 (Gemma 4 26B-A4B)

In short:

- **Identical inputs gave identical micro-ops**, in all 100 frozen cycles.
- **The two `call` steps are constants.** They name no register that holds anything, so their input
  never changes and neither does their micro-op.
- **A judgement follows its reading.** The memory reading was the same in every live run and was
  always judged fine. The temperature took 9 values: it was judged fine at 73, 74 and 78 °C and
  worrying at 80 °C and above, in every case.
- **The free-text step varied with its inputs**: 7 different sentences in 20 live runs.
- **Memory answered 85 of 100 cycles.** Time in the decoder fell from 9.0 seconds a run to 1.6 on
  average, and from the seventh run on the model was not called at all.
- **A cycle sent to the model takes 1.3 to 2.6 seconds.** Most of that is writing the micro-op,
  which is about 43 tokens of JSON.

### Live: every run reads the machine again

| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |
|---|---|---|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 1 | 20/20 | 1 | 1.33 |
| 2 | Read the temperature and store it in `temp`. | 1 | 20/20 | 1 | 1.32 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 1 | 20/20 | 1 | 1.87 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 2 | 16/20 | 9 | 1.92 |
| 5 | Write one short sentence for the user. If `mem_level` and `temp_level` are both fine, say the machine looks healthy. Otherwise name each worrying reading and quote it from `mem` or `temp`. Store the sentence in `verdict`. | 7 | 6/20 | 9 | 2.59 |

- Step 3 stored "fine" (20)
- Step 4 stored "worrying" (16); "fine" (4)
- Step 5 stored "The machine is not healthy because the temperature is Temperature: 85 °C at the hottest sensor (acpitz)." (6); "The machine is not healthy because the temperature is Temperature: 86 °C at the hottest sensor (acpitz)." (5); "the machine looks healthy" (4); "The machine is not healthy because the temperature is Temperature: 88 °C at the hottest sensor (acpitz)." (2); and 3 more

### Frozen: every run replays the first run's command outputs

| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |
|---|---|---|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 1 | 20/20 | 1 | 1.32 |
| 2 | Read the temperature and store it in `temp`. | 1 | 20/20 | 1 | 1.32 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 1 | 20/20 | 1 | 1.87 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 1 | 20/20 | 1 | 1.92 |
| 5 | Write one short sentence for the user. If `mem_level` and `temp_level` are both fine, say the machine looks healthy. Otherwise name each worrying reading and quote it from `mem` or `temp`. Store the sentence in `verdict`. | 1 | 20/20 | 1 | 2.59 |

- Step 3 stored "fine" (20)
- Step 4 stored "worrying" (20)
- Step 5 stored "The machine is not healthy because the temperature is Temperature: 86 °C at the hottest sensor (acpitz)." (20)

### With memory: every run reads the machine again, memory starts empty

| Step | Instruction | Runs answered from memory |
|---|---|---|
| 1 | Read the memory status and store it in `mem`. | 19/20 |
| 2 | Read the temperature and store it in `temp`. | 19/20 |
| 3 | Judge whether the memory reading in `mem` looks healthy for a machine in use. Store `fine` or `worrying` in `mem_level`. | 18/20 |
| 4 | Judge whether the temperature in `temp` looks healthy for a machine in use. Store `fine` or `worrying` in `temp_level`. | 15/20 |
| 5 | Write one short sentence for the user. If `mem_level` and `temp_level` are both fine, say the machine looks healthy. Otherwise name each worrying reading and quote it from `mem` or `temp`. Store the sentence in `verdict`. | 14/20 |

Memory answered 85 of 100 cycles. Time a run spends in the decoder: 9.0 s without memory (median); with it, 1.6 s on average and 0.0 s at the median.

## What it means

- Decoding is repeatable enough to remember: nothing varied that did not have a reason to.
- Memory needs each instruction to see as little as possible. Shown every register, step 2's input
  changed whenever the memory reading did, and it could never be remembered. Shown only the
  registers it names, it is a constant.
- Readings come from a small set of values, so memory fills in a judgement one value at a time.
  After a few runs it holds the answer for every temperature the machine has shown.

## Caveats

- One program, one model, one machine.
- Stable is not the same as right. Nobody checked whether 80 °C is the right place to start
  worrying on this laptop, and an earlier version of the decoder's prompt drew the line lower: it
  called 74 °C worrying.
- The memory result flatters steady conditions. The runs were back to back, and once the model
  stopped being called the machine settled at one temperature, so the readings repeated.
- The check heats the machine it is checking: while every step went to the model the temperature
  sat at 80 to 88 °C.
- A remembered decode is as old as the settings it was made under. Changing how the server places
  the model can change answers, and memory does not notice.
