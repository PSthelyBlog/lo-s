import pathlib
import re
import tempfile
import unittest

from los import split, tune
from los.tune import Result

# What llama-server prints with -lv 5, cut down: one trial load while it looks for a split that
# fits, then the load it used.
LOG = """\
0.01.000 I load_tensors: loading model tensors, this can take a while... (load_mode = none)
0.01.100 D tensor blk.0.ffn_down_exps.weight (136 MiB q4_0) buffer type overridden to CUDA_Host
0.01.200 D tensor blk.1.ffn_down_exps.weight (136 MiB q4_0) buffer type overridden to CUDA_Host
0.01.300 I load_tensors: offloaded 3/3 layers to GPU
0.01.400 I load_tensors:        CUDA0 model buffer size =     0.00 MiB
0.02.000 I load_tensors: loading model tensors, this can take a while... (load_mode = mmap)
0.02.100 D tensor blk.1.ffn_gate.weight (3 MiB q4_0) buffer type overridden to CUDA_Host
0.02.200 D tensor blk.1.ffn_down_exps.weight (136 MiB q4_0) buffer type overridden to CUDA_Host
0.02.300 D tensor blk.10.ffn_down_exps.weight (136 MiB q4_0) buffer type overridden to CPU
0.02.400 D tensor token_embd.weight (700 MiB q6_K) buffer type overridden to CUDA_Host
0.02.500 I load_tensors: offloading output layer to GPU
0.02.600 I load_tensors: offloaded 3/3 layers to GPU
0.02.700 I load_tensors:   CPU_Mapped model buffer size = 13752.09 MiB
0.02.800 I load_tensors:        CUDA0 model buffer size =  5580.13 MiB
0.02.900 I load_tensors:    CUDA_Host model buffer size =    20.50 MiB
"""
KEPT = ["blk.1.ffn_gate.weight", "blk.1.ffn_down_exps.weight", "blk.10.ffn_down_exps.weight", "token_embd.weight"]


class SplitTest(unittest.TestCase):
    def test_read_takes_the_load_that_was_used_not_the_trial_ones(self):
        self.assertEqual(split.read(LOG), (3, KEPT, 5580))

    def test_read_refuses_a_log_that_does_not_say_where_the_layers_went(self):
        with self.assertRaises(ValueError):
            split.read("0.00.100 I srv  llama_server: model loaded\n")

    def test_patterns_match_the_kept_tensors_and_no_others(self):
        patterns = [re.compile(pattern) for pattern in split.patterns(KEPT)]
        self.assertEqual(len(patterns), 3)                  # the two blocks of ffn_down_exps share one
        others = ["blk.0.ffn_down_exps.weight", "blk.11.ffn_down_exps.weight", "blk.100.ffn_down_exps.weight",
                  "blk.1.ffn_gate_inp.weight", "blk.10.ffn_gate.weight", "blk.1.ffn_down_exps.scale",
                  "blk.1.ffn_gateXweight", "token_embd.weight.extra"]
        for name in KEPT:
            self.assertEqual(sum(bool(pattern.search(name)) for pattern in patterns), 1, name)
        for name in others:
            self.assertFalse(any(pattern.search(name) for pattern in patterns), name)

    def test_arguments_turn_fitting_off_and_name_every_kept_tensor(self):
        args = split.arguments(3, KEPT)
        self.assertEqual(args[:5], ["--fit", "off", "--gpu-layers", "3", "--override-tensor"])
        self.assertEqual(args[5].split(","), [pattern + "=CPU" for pattern in split.patterns(KEPT)])
        self.assertEqual(split.arguments(13, []), ["--fit", "off", "--gpu-layers", "13"])

    def test_the_written_file_is_one_line_of_arguments_under_its_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = split.write("m.gguf", 3, KEPT, 5580, folder)
            self.assertEqual(path, pathlib.Path(folder, "m.gguf.split"))
            lines = [line for line in path.read_text().splitlines() if not line.startswith("#")]
            self.assertEqual(lines, [" ".join(split.arguments(3, KEPT))])
            self.assertNotIn(" ", split.arguments(3, KEPT)[5])      # serve.sh splits the line on spaces

    def test_new_tuned_settings_drop_the_split_chosen_under_the_old_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            chosen = split.write("m.gguf", 3, KEPT, 5580, folder)
            tune.apply("m.gguf", Result([], seconds=0.7), folder)                   # nothing changes
            self.assertTrue(chosen.exists())
            tune.apply("m.gguf", Result(["--cpu-moe"], seconds=0.6), folder)
            self.assertFalse(chosen.exists())
