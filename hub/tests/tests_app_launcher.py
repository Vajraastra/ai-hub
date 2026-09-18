import os
import shlex
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.app_launcher import _serialize_commandline_args


class AppLauncherTests(unittest.TestCase):
    def test_commandline_args_preserve_windows_paths_and_spaces(self):
        args = [
            "--ckpt-dirs",
            r"E:\Models\checkpoints",
            "--lora-dirs",
            r"E:\Model Store\loras",
        ]

        serialized = _serialize_commandline_args(args)

        self.assertEqual(shlex.split(serialized), args)


if __name__ == "__main__":
    unittest.main()
