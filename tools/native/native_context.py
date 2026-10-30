"""Bound native commands and keep developer SDKs outside application payloads."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys


def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def native_target():
    target = {("Darwin", "arm64"): "macos-arm64", ("Linux", "x86_64"): "linux-x64",
              ("Windows", "AMD64"): "windows-x64"}.get((platform.system(), platform.machine()))
    if not target:
        raise ValueError("A supported native media host is required")
    return target


def shell_path(path):
    text = Path(path).resolve().as_posix()
    if re.match(r"^[A-Za-z]:/", text):
        return "/" + text[0].lower() + text[2:]
    return text


def sdk_paths(environment):
    return [Path(environment[name]).resolve() / "usr/bin" for name in
            ("SYNCWATCH_VLC_EXTRA_TOOLS_ROOT", "SYNCWATCH_VLC_AUTOTOOLS_ROOT") if environment.get(name)]


class BuildContext:
    def __init__(self, work, epoch, *, autotools=True):
        self.work, self.target = work, native_target()
        self.logs = work / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.prefix = work / "prefix"
        self.prefix.mkdir(exist_ok=True)
        self.commands = {}
        self.env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), PYTHONDONTWRITEBYTECODE="1", LC_ALL="C",
                        CONFIG_SITE="/dev/null", GCRYPT_CONFIG="false",
                        GIT_CEILING_DIRECTORIES=str(work.resolve()))
        paths = []
        if self.target == "windows-x64":
            root = Path(os.environ.get("SYNCWATCH_VLC_MSYS_ROOT",
                        Path(__file__).resolve().parents[2] / ".local/vlc-sdk/windows/msys64")).resolve()
            self.msys = root
            paths.extend([root / "ucrt64/bin", root / "usr/bin"])
            self.shell = str(root / "usr/bin/bash.exe")
            self.env.update(MSYSTEM="UCRT64", MSYS2_PATH_TYPE="minimal", CHERE_INVOKING="1")
        else:
            paths.extend(sdk_paths(self.env))
            if self.target == "macos-arm64":
                paths.extend(Path("/opt/homebrew/opt") / name / "bin" for name in ["m4", "bison", "flex", "gettext"])
                paths.append(Path("/opt/homebrew/bin"))
            self.shell = "/bin/sh"
        self.env["PATH"] = os.pathsep.join(str(p) for p in paths) + os.pathsep + self.env["PATH"]
        if autotools:
            for name, key in [("m4", "M4"), ("autoconf", "AUTOCONF"), ("automake", "AUTOMAKE"),
                              ("aclocal", "ACLOCAL"), ("autoreconf", "AUTORECONF"), ("autom4te", "AUTOM4TE"),
                              ("autoheader", "AUTOHEADER"),
                              ("glibtoolize" if self.target == "macos-arm64" else "libtoolize", "LIBTOOLIZE")]:
                path = self.tool(name)
                self.env[key] = shell_path(path) if self.target == "windows-x64" else path
        prefix = self.prefix.as_posix()
        self.env["PATH"] = str(self.prefix / "bin") + os.pathsep + self.env["PATH"]
        self.env.update(PKG_CONFIG_PATH=prefix + "/lib/pkgconfig",
                        PKG_CONFIG_LIBDIR=prefix + "/lib/pkgconfig",
                        CFLAGS="-O2 -g0", CXXFLAGS="-O2 -g0", CPPFLAGS="-I" + prefix + "/include",
                        LDFLAGS="-L" + prefix + "/lib", CC="gcc", CXX="g++")
        if self.target == "macos-arm64":
            self.env.update(CC="clang", CXX="clang++", OBJC="clang", MACOSX_DEPLOYMENT_TARGET="13.0",
                            ac_cv_func_pipe2="no", ac_cv_func_dup3="no")
            for key in ["CFLAGS", "CXXFLAGS"]:
                self.env[key] += " -mmacosx-version-min=13.0"
            self.env["LDFLAGS"] += " -Wl,-rpath,@loader_path -Wl,-rpath,@loader_path/../../.."
        elif self.target == "windows-x64":
            self.env["LDFLAGS"] += " -static-libgcc -static-libstdc++"
            # Make configure see the same API floor as the modern MinGW SDK.
            # Otherwise its preprocessor-only probe misses libsynchronization.
            self.env["CPPFLAGS"] += " -D_WIN32_WINNT=0x0A00"
            # The UCRT compiler also produces native build tools. The POSIX
            # shell triplet must not make configure search for an MSYS C ABI.
            self.env["BUILDCC"] = self.env["CC"]
        else:
            self.env["LD_LIBRARY_PATH"] = prefix + "/lib"
            self.env["LDFLAGS"] += " -Wl,-rpath-link," + prefix + "/lib"
        for name, relative in [("gettext_datadir", "usr/share/gettext"), ("BISON_PKGDATADIR", "usr/share/bison")]:
            root = self.env.get("SYNCWATCH_VLC_EXTRA_TOOLS_ROOT")
            if root:
                self.env[name] = str(Path(root) / relative)
        # Relocated Linux autotools retain their package-relative Perl/macro data.
        root = self.env.get("SYNCWATCH_VLC_AUTOTOOLS_ROOT")
        if root:
            root = Path(root)
            macros = [root / "usr/share/aclocal", root / "usr/share/libtool/m4", Path("/usr/share/aclocal")]
            extra = self.env.get("SYNCWATCH_VLC_EXTRA_TOOLS_ROOT")
            if extra:
                macros.insert(0, Path(extra) / "usr/share/aclocal")
            self.env["ACLOCAL_PATH"] = os.pathsep.join(str(p) for p in macros)
            perl = [str(p) for p in (root / "usr/share").glob("automake-*")]
            if perl:
                self.env["PERL5LIB"] = os.pathsep.join(perl + [str(root / "usr/share/autoconf")])
                if len(perl) != 1:
                    raise ValueError("Select one relocated Automake data package")
                self.env["AUTOMAKE_LIBDIR"] = perl[0]
                self.env["ACLOCAL_AUTOMAKE_DIR"] = str(root / "usr/share" / Path(perl[0]).name.replace("automake-", "aclocal-"))
            self.env["AUTOM4TE_CFG"] = str(root / "usr/share/autoconf/autom4te.cfg")
            self.env["AC_MACRODIR"] = str(root / "usr/share/autoconf")

    def tool(self, name):
        if self.target == "windows-x64":
            script = self.msys / "usr/bin" / name
            # Native Windows discovery uses PATHEXT. GNU SDK shell scripts
            # deliberately have no .exe/.cmd suffix and execute through Bash.
            if script.is_file():
                return str(script)
        path = shutil.which(name, path=self.env["PATH"])
        if not path:
            raise ValueError("Missing native developer tool: " + name)
        return path

    def sdk_command(self, name, *arguments):
        path = self.tool(name)
        if self.target == "windows-x64":
            return [self.shell, "-c", shlex.join([shell_path(path), *arguments])]
        root = self.env.get("SYNCWATCH_VLC_EXTRA_TOOLS_ROOT")
        if self.target == "linux-x64" and root and name in {"msgfmt", "msginit", "msgmerge", "xgettext"}:
            directory = Path(root).resolve() / "usr/lib"
            libraries = [directory, *sorted(p for p in directory.iterdir() if p.is_dir())]
            # This environment belongs to this SDK tool invocation only. Target
            # library discovery and application consumers must not inherit it.
            return ["/usr/bin/env", "LD_LIBRARY_PATH=" + os.pathsep.join(map(str, libraries)), path, *arguments]
        return [path, *arguments]

    def meson_binaries(self):
        if self.target == "linux-x64" and self.env.get("SYNCWATCH_VLC_EXTRA_TOOLS_ROOT"):
            return {name: self.sdk_command(name) for name in ("msgfmt", "msginit", "msgmerge", "xgettext")}
        return {}

    def sdk_support(self):
        root = self.env.get("SYNCWATCH_VLC_EXTRA_TOOLS_ROOT")
        if self.target != "linux-x64" or not root:
            return {}
        root = Path(root).resolve()
        files = {}
        for path in sorted((root / "usr/lib").rglob("*.so*")):
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise ValueError("SDK tool support leaves its declared root")
                files[path.relative_to(root).as_posix()] = checksum(path)
        return files

    def auxiliary_files(self):
        if self.target != "linux-x64":
            return {}
        roots = [Path("/usr/share/misc")]
        for variable in ["SYNCWATCH_VLC_AUTOTOOLS_ROOT", "SYNCWATCH_VLC_EXTRA_TOOLS_ROOT"]:
            if self.env.get(variable):
                roots.insert(0, Path(self.env[variable]) / "usr/share/misc")
        files = {}
        for name in ["config.guess", "config.sub"]:
            path = next((root / name for root in roots if (root / name).is_file()), None)
            if path is None:
                raise ValueError("The native Autotools SDK requires autotools-dev: " + name)
            files[name] = path
        return files

    def compiler_runtime(self):
        if self.target != "windows-x64":
            return {}
        result = {}
        for name in ["libstdc++.a", "libgcc.a", "libgcc_eh.a"]:
            path = Path(subprocess.check_output([self.tool(self.env["CXX"]), "-print-file-name=" + name],
                                               env=self.env, text=True).strip())
            if not path.is_absolute() or not path.is_file():
                raise ValueError("Missing static compiler support archive: " + name)
            result[name] = path
        return result

    def run(self, label, args, cwd=None, shell=False, env=None, timeout=3600):
        if label in self.commands:
            raise ValueError("Build command labels must be unique")
        directory = cwd or self.work
        if shell:
            args = [self.shell, "-c", "cd " + shlex.quote(shell_path(directory)) + " && " + shlex.join(args)]
        output = self.logs / (label + ".log")
        environment = dict(self.env, **(env or {}))
        print(label, flush=True)
        with output.open("wb") as log:
            process = subprocess.run(args, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        self.commands[label] = {"args": args, "logSha256": checksum(output), "exitCode": process.returncode,
                                "configuration": {key: environment[key] for key in
                                    ["CC", "CXX", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "SOURCE_DATE_EPOCH", "LC_ALL"]}}
        (self.work / "commands.json").write_text(json.dumps(self.commands, indent=2) + "\n")
        if process.returncode:
            raise ValueError(label + " failed:\n" + output.read_bytes()[-12000:].decode("utf-8", errors="replace"))

    def sdk(self):
        tools = {}
        names = [self.env["CC"], self.env["CXX"], "make", "autoconf", "automake",
                 "libtoolize" if self.target != "macos-arm64" else "glibtoolize", "pkg-config", "ninja",
                 "cmake", "bison", "flex", "git", "m4", "gperf"]
        if self.target != "macos-arm64":
            names.append("nasm")
        if self.target == "linux-x64":
            names.extend(["readelf", "patchelf", "msgfmt", "msginit", "msgmerge", "xgettext"])
        elif self.target == "windows-x64":
            names.append("objdump")
        for name in names:
            path = self.tool(name)
            command = self.sdk_command(name, "--version")
            result = subprocess.check_output(command, env=self.env, stderr=subprocess.STDOUT, timeout=30)
            tools[name] = {"sha256": checksum(path), "version": result.decode(errors="replace").splitlines()[0]}
        tools["buildPython"] = {"sha256": checksum(sys.executable), "version": sys.version.split()[0]}
        platform_sdk = {}
        if self.target == "macos-arm64":
            # /usr/bin compiler commands are SDK-selection shims. Measure the
            # actual compiler and SDK metadata, as well as those entry points.
            compiler = subprocess.check_output(["xcrun", "--find", "clang"], text=True).strip()
            root = Path(subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip())
            platform_sdk = {"compilerSha256": checksum(compiler),
                            "settingsSha256": checksum(root / "SDKSettings.json"),
                            "version": subprocess.check_output(["xcrun", "--show-sdk-version"], text=True).strip()}
            for name in ["otool", "install_name_tool", "codesign"]:
                platform_sdk[name] = checksum(self.tool(name))
        else:
            platform_sdk["compilerTarget"] = subprocess.check_output([self.tool(self.env["CC"]), "-dumpmachine"],
                                                                     env=self.env, text=True).strip()
        return {"target": self.target, "tools": tools, "sdkPackaged": False,
                "platformSdk": platform_sdk,
                "compilerSupport": {name: checksum(path) for name, path in self.compiler_runtime().items()},
                "toolSupport": self.sdk_support(),
                "gnuConfig": {name: checksum(path) for name, path in self.auxiliary_files().items()}}
