import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import git_transport


class GitTransportTests(unittest.TestCase):
    def exercise(self, results, arguments=None, **options):
        arguments = arguments or ["checkout", "--detach", "a" * 40]
        with patch.object(git_transport.subprocess, "run", side_effect=results) as command, \
                patch.object(git_transport.time, "sleep") as wait, contextlib.redirect_stderr(io.StringIO()):
            result = git_transport.run(arguments, cwd=Path("owned-source"), **options)
        return result, command.call_args_list, wait.call_args_list

    def result(self, code, error=b"", data=b""):
        return subprocess.CompletedProcess([], code, stdout=data, stderr=error)

    def test_server_failure_replays_exact_pin_and_utf8_patterns(self):
        results = [self.result(128, b"fatal: The requested URL returned error: 500"),
                   self.result(128, b"error: RPC failed; HTTP 503 curl 22"), self.result(0)]
        arguments = ["-c", "core.longpaths=true", "sparse-checkout", "set", "--no-cone", "--stdin"]
        _, calls, waits = self.exercise(results, arguments, input="/movie-映画.txt\n")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call == calls[0] for call in calls))
        self.assertEqual(calls[0].args[0], ["git", *arguments])
        self.assertEqual(calls[0].kwargs["input"], "/movie-映画.txt\n".encode("utf-8"))
        self.assertEqual([call.args[0] for call in waits], [1, 2])

    def test_binary_object_reads_are_preserved(self):
        payload = b"\0\xff\x81source\r\n"
        result, calls, waits = self.exercise([self.result(0, data=payload)], capture_output=True)
        self.assertEqual(result.stdout, payload)
        self.assertEqual(calls[0].kwargs["stdout"], subprocess.PIPE)
        self.assertFalse(waits)

    def test_http2_stream_reset_retries_the_same_immutable_binary_object_read(self):
        error = b"error: RPC failed; curl 92 HTTP/2 stream 3 was not closed cleanly: PROTOCOL_ERROR (err 1)"
        payload = b"\0\xfforiginal source\r\n"
        arguments = ["cat-file", "blob", "a" * 40 + ":src/source.c"]
        result, calls, waits = self.exercise([self.result(128, error), self.result(0, data=payload)],
                                            arguments, capture_output=True)
        self.assertEqual(result.stdout, payload)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1].args[0], ["git", *arguments])
        self.assertEqual([call.args[0] for call in waits], [1])

    def test_http2_diagnostic_does_not_override_an_object_integrity_failure(self):
        error = b"curl 92 HTTP/2 stream error\nfatal: object hash mismatch"
        with patch.object(git_transport.subprocess, "run", return_value=self.result(128, error)) as command, \
                patch.object(git_transport.time, "sleep") as wait, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                git_transport.run(["cat-file", "blob", "a" * 40], cwd=Path("owned-source"))
            self.assertEqual(command.call_count, 1)
            wait.assert_not_called()

    def test_git_diagnostics_do_not_depend_on_python_text_code_page(self):
        binary = io.BytesIO()
        stream = io.TextIOWrapper(binary, encoding="ascii")
        message = "source-映画\n".encode("utf-8")
        with patch.object(git_transport.sys, "stderr", stream):
            git_transport.emit_error(message)
        self.assertEqual(binary.getvalue(), message)
        stream.detach()

    def test_permanent_errors_and_cancellation_are_not_retried(self):
        failures = [self.result(128, b"fatal: not our ref " + b"a" * 40),
                    self.result(128, b"The requested URL returned error: 403"),
                    self.result(128, b"The requested URL returned error: 404"),
                    self.result(128, b"The requested URL returned error: 501"),
                    self.result(-15, b"The requested URL returned error: 500"),
                    self.result(130, b"The requested URL returned error: 500"),
                    self.result(128, b"HTTP 500\nfatal: not our ref " + b"a" * 40)]
        for failure in failures:
            with self.subTest(error=failure.stderr), patch.object(git_transport.subprocess, "run", return_value=failure) as command, \
                    patch.object(git_transport.time, "sleep") as wait, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    git_transport.run(["fetch", "origin", "a" * 40], cwd=Path("owned-source"))
                self.assertEqual(command.call_count, 1)
                wait.assert_not_called()

    def test_fetch_retry_keeps_the_exact_revision(self):
        revision = "a" * 40
        arguments = ["fetch", "--filter=blob:none", "--depth=1", "--no-tags", "origin", revision]
        _, calls, _ = self.exercise([self.result(128, b"fatal: HTTP 504"), self.result(0)], arguments)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1].args[0][-1], revision)

    def test_server_failures_stop_after_three_attempts(self):
        with patch.object(git_transport.subprocess, "run", return_value=self.result(128, b"The requested URL returned error: 502")) as command, \
                patch.object(git_transport.time, "sleep") as wait, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                git_transport.run(["clone", "--no-checkout", "--", "https://example.invalid/source", "owned-source"], cwd=Path("."))
            self.assertEqual(command.call_count, 3)
            self.assertEqual(wait.call_count, 2)
            self.assertEqual(error.exception.returncode, 128)


if __name__ == "__main__":
    unittest.main()
