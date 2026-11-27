"""Configure narrow upstream injection/lifecycle hooks and headless defaults."""

import ast
from pathlib import Path


def configure(source):
    path = source / "syncplay/client.py"
    text = path.read_text(encoding="utf-8")
    replacements = [
        ("class SyncplayClient(object):\n", "class SyncplayClient(object):\n"
         "    # SyncWatch 2026-10-22: injectable transport and externally owned reactor.\n"
         "    protocolFactoryType = SyncClientFactory\n    ownsReactor = True\n", 1),
        ("self.protocolFactory = SyncClientFactory(self)", "self.protocolFactory = self.protocolFactoryType(self)", 2),
        ("        reactor.run()\n", "        if self.ownsReactor:\n            reactor.run()\n", 1),
        ("        reactor.callLater(0.1, reactor.stop)\n",
         "        if self.ownsReactor:\n            reactor.callLater(0.1, reactor.stop)\n", 1),
    ]
    for before, after, count in replacements:
        if text.count(before) != count:
            raise ValueError("Original upstream headless integration boundary differs")
        text = text.replace(before, after)
    before = "    def updateFile(self, filename, duration, path):"
    if text.count(before) != 1:
        raise ValueError("Original engine media callback differs")
    text = text.replace(before, "    def updateFile(self, filename, duration, path, fileSize=None):")
    before = """        try:
            size = os.path.getsize(path)
        except:
            try:
                path = path.decode('utf-8')
                size = os.path.getsize(path)
            except:
                size = 0
"""
    if text.count(before) != 1:
        raise ValueError("Original media-size lookup differs")
    text = text.replace(before, "        # SyncWatch: native media metadata avoids resolving opaque local handles here.\n"
        "        if fileSize is None:\n" + "".join("    " + line + "\n" for line in before.splitlines())
        + "        else:\n            size = fileSize\n")
    # Native status observations and explicit user actions have different
    # authority. Preserve upstream state/correction handling while permitting
    # only the latter to enter its existing user-change detection/send path.
    before = "    def updatePlayerStatus(self, paused, position):"
    if text.count(before) != 1:
        raise ValueError("Original native observation API differs")
    text = text.replace(before, "    def updatePlayerStatus(self, paused, position, fromUser=True):")
    before = "        pauseChange, seeked = self._determinePlayerStateChange(paused, position)"
    if text.count(before) != 1:
        raise ValueError("Original user-state detection boundary differs")
    text = text.replace(before, "        # SyncWatch: actual observations are not implicit user commands.\n"
        "        pauseChange, seeked = self._determinePlayerStateChange(paused, position) if fromUser else (False, False)")
    path.write_text(text, encoding="utf-8", newline="\n")


def defaults(source):
    tree = ast.parse((source / "syncplay/ui/ConfigurationGetter.py").read_bytes())
    values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                      and t.value.id == "self" and t.attr == "_config" for t in node.targets)
              and isinstance(node.value, ast.Dict)]
    if len(values) != 1:
        raise ValueError("Original engine configuration defaults differ")

    def encode(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.List):
            return [encode(n) for n in node.elts]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            return -node.operand.value
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "constants":
            return {"constant": node.attr}
        raise ValueError("Unknown expression in original engine defaults")

    return {ast.literal_eval(key): encode(value) for key, value in zip(values[0].keys, values[0].values)}
