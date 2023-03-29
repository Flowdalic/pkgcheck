from pathlib import Path

from .test_pkgdir import PkgDirCheckBase
from pkgcheck.checks.unused_file import PotentiallyUnusedFile, UnusedFileCheck

class TestUnusedFile(PkgDirCheckBase):
    """Check UnusedFile results."""

    check_kls = UnusedFileCheck

    def test_single_unused_file(self):
        category = "unused-file"
        package = "SingleUnusedFile"
        version = "0.1"

        pkg = self.mk_pkg({"this-is-a-unused.init": "nobody needs me"}, category, package, version)
        pkg_path = Path(pkg.path)
        empty_ebuild = pkg_path.parent / f"{pkg.package}-{version}.ebuild"
        empty_ebuild.touch()

        r = self.assertReport(self.mk_check(), [pkg])
        assert isinstance(r, PotentiallyUnusedFile)
