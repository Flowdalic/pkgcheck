import re
from pathlib import Path

from . import OptionalCheck
from .. import results, sources

class PotentiallyUnusedFile(results.PackageResult, results.Warning):
    """Potentially unused file in FILESDIR."""

    def __init__(self, file, **kwargs):
        super().__init__(**kwargs)
        self.file = file

    @property
    def desc(self):
        return f"potentially unused file in FILESDIR: {self.file}"


class UnusedFileCheck(OptionalCheck):
    """(Potentially) Unused files in FILESDIR."""

    _source = sources.PackageRepoSource

    known_results = frozenset(
        [
            PotentiallyUnusedFile,
        ]
    )

    pms_ver_re = r"^([0-9]+(\.[0-9]+)*)([a-z]?)((_(alpha|beta|pre|rc|p)[0-9]*)*)(-r[0-9]+)?"

    def __init__(self, *args):
        super().__init__(*args)

    def feed(self, pkgs):
        # Use the first package as reference.
        pkg = pkgs[0]
        pkg_path = Path(self.options.target_repo.location) / pkg.category / pkg.package
        filesdir = pkg_path / "files"
        if not filesdir.is_dir():
            return

        # TODO: only run the check if PATCHES of all of pkg's ebuilds does not contain any globs.

        for path in filesdir.iterdir():
            # Ignore non-files in filesdir.
            if not path.is_file():
                continue

            filename = path.name
            actual_filename = filename

            if filename.startswith(pkg.package):
                filename = filename[len(pkg.package):]
                if filename and filename[0] == "-":
                    filename = filename[1:]

            # Strip a potential version prefix.
            filename = re.sub(self.pms_ver_re, "", filename)

            # Avoid false positives by ignoring short filenames.
            if len(filename) < 16:
                continue

            file_used = False
            for ebuild in pkgs:
                ebuild_path = Path(ebuild.path)
                if filename in ebuild_path.read_text():
                    file_used = True
                    break

            if file_used:
                continue

            yield PotentiallyUnusedFile(actual_filename, pkg=pkg)
