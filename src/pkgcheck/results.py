"""Base classes for check results."""

from pkgcore.ebuild import cpv
from snakeoil import klass

from . import base
from .packages import FilteredPkg, RawCPV


class Result:
    """Generic report result returned from a check."""

    # all results are shown by default
    _filtered = False
    # default to repository level results
    scope = base.repo_scope
    # priority level and color
    level = None
    color = None

    def __str__(self):
        return self.desc

    @property
    def desc(self):
        """Result description."""

    @property
    def _attrs(self):
        """Return all public result attributes."""
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    @staticmethod
    def attrs_to_pkg(d):
        """Reconstruct a package object from split attributes."""
        category = d.pop('category', None)
        package = d.pop('package', None)
        version = d.pop('version', None)
        if any((category, package, version)):
            pkg = RawCPV(category, package, version)
            d['pkg'] = pkg
        return d

    def __eq__(self, other):
        return self._attrs == other._attrs

    def __hash__(self):
        return hash(tuple(sorted(self._attrs)))

    def __lt__(self, other):
        if self.scope is other.scope:
            if self.__class__.__name__ == other.__class__.__name__:
                return self.desc < other.desc
            return self.__class__.__name__ < other.__class__.__name__
        return self.scope < other.scope


class Error(Result):
    """Result with an error priority level."""

    level = 'error'
    color = 'red'


class Warning(Result):
    """Result with a warning priority level."""

    level = 'warning'
    color = 'yellow'


class Info(Result):
    """Result with an info priority level."""

    level = 'info'
    color = 'green'


class CommitResult(Result):
    """Result related to a specific git commit."""

    scope = base.commit_scope

    def __init__(self, commit, **kwargs):
        super().__init__(**kwargs)
        self.commit = commit
        self._attr = 'commit'

    def __lt__(self, other):
        try:
            # if hashes match, sort by name/desc
            if self.commit == other.commit:
                if self.__class__.__name__ == other.__class__.__name__:
                    return self.desc < other.desc
                return self.__class__.__name__ < other.__class__.__name__
        except AttributeError as e:
            pass
        return False


class EclassResult(Result):
    """Result related to a specific eclass."""

    scope = base.eclass_scope

    def __init__(self, eclass, **kwargs):
        super().__init__(**kwargs)
        self.eclass = str(eclass)
        self._attr = 'eclass'

    def __lt__(self, other):
        try:
            # if eclasses match, sort by name/desc
            if self.eclass == other.eclass:
                if self.__class__.__name__ == other.__class__.__name__:
                    return self.desc < other.desc
                return self.__class__.__name__ < other.__class__.__name__
            return self.eclass < other.eclass
        except AttributeError as e:
            pass
        return False


class CategoryResult(Result):
    """Result related to a specific category."""

    scope = base.category_scope

    def __init__(self, pkg, **kwargs):
        super().__init__(**kwargs)
        self.category = pkg.category
        self._attr = 'category'

    def __lt__(self, other):
        try:
            if self.category != other.category:
                return self.category < other.category
        except AttributeError:
            pass
        return super().__lt__(other)


class PackageResult(CategoryResult):
    """Result related to a specific package."""

    scope = base.package_scope

    def __init__(self, pkg, **kwargs):
        super().__init__(pkg, **kwargs)
        self.package = pkg.package
        self._attr = 'package'

    def __lt__(self, other):
        try:
            if self.package != other.package:
                return self.package < other.package
        except AttributeError:
            pass
        return super().__lt__(other)


class VersionResult(PackageResult):
    """Result related to a specific version of a package."""

    scope = base.version_scope

    def __init__(self, pkg, **kwargs):
        super().__init__(pkg, **kwargs)
        self.version = pkg.fullver
        self._attr = 'version'

    @klass.jit_attr
    def ver_rev(self):
        version, _, revision = self.version.partition('-r')
        revision = cpv._Revision(revision)
        return version, revision

    def __lt__(self, other, cmp=None):
        try:
            if cmp is None:
                cmp = cpv.ver_cmp(*(self.ver_rev + other.ver_rev))
            if cmp < 0:
                return True
            elif cmp > 0:
                return False
        except AttributeError:
            pass
        return super().__lt__(other)


class LineResult(VersionResult):
    """Result related to a specific line of an ebuild."""

    def __init__(self, line, lineno, **kwargs):
        super().__init__(**kwargs)
        self.line = line
        self.lineno = lineno

    def __lt__(self, other):
        cmp = None
        try:
            # sort by line number for matching versions
            cmp = cpv.ver_cmp(*(self.ver_rev + other.ver_rev))
            if cmp == 0:
                if self.lineno < other.lineno:
                    return True
                elif self.lineno > other.lineno:
                    return False
        except AttributeError:
            pass
        return super().__lt__(other, cmp=cmp)


class FilteredVersionResult(VersionResult):
    """Result that will be optionally filtered for old packages by default."""

    def __init__(self, pkg, **kwargs):
        if isinstance(pkg, FilteredPkg):
            self._filtered = True
            pkg = pkg._pkg
        super().__init__(pkg, **kwargs)


class _LogResult(Result):
    """Message caught from a logger instance."""

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    @property
    def desc(self):
        return self.msg


class LogWarning(_LogResult, Warning):
    """Warning caught from a logger instance."""


class LogError(_LogResult, Error):
    """Error caught from a logger instance."""


class _RegisterMetadataErrors(type):
    """Metaclass for registering known metadata results."""

    def __new__(cls, name, bases, class_dict):
        new_cls = type.__new__(cls, name, bases, class_dict)
        attr = new_cls._attr
        if attr is not None:
            new_cls.results.add(new_cls)
            setting = new_cls.result_mapping.setdefault(attr, new_cls)
            if setting != new_cls:
                raise ValueError(
                    f'metadata attribute {attr!r} already registered: {setting!r}')
        elif new_cls.__name__ != 'MetadataError':
            raise ValueError(f'class missing metadata attributes: {new_cls!r}')
        return new_cls


class MetadataError(VersionResult, Error, metaclass=_RegisterMetadataErrors):
    """Problem detected with a package's metadata."""

    # specific metadata attributes handled by the result class
    _attr = None
    # mapping from data attributes to result classes
    result_mapping = {}
    # set of registered result classes
    results = set()

    def __init__(self, attr, msg, **kwargs):
        super().__init__(**kwargs)
        self.attr = attr
        self.msg = str(msg)

    @property
    def desc(self):
        if self._attr is not None:
            return self.msg
        else:
            return f'attr({self.attr}): {self.msg}'


class IncorrectCopyright(Warning):
    """Changed file with incorrect copyright date."""

    def __init__(self, year, line, **kwargs):
        super().__init__(**kwargs)
        self.year = year
        self.line = line

    @property
    def desc(self):
        return f'incorrect copyright year {self.year}: {self.line!r}'


class _FileHeaderResult(Result):
    """Generic file header result."""

    def __init__(self, line, **kwargs):
        super().__init__(**kwargs)
        self.line = line


class InvalidCopyright(_FileHeaderResult, Error):
    """File with invalid copyright.

    The file does not start with a valid copyright line. Each ebuild or eclass
    file must start with a copyright line of the form:

        # Copyright YEARS MAIN-CONTRIBUTOR [OTHER-CONTRIBUTOR]... [and others]

    Files in the Gentoo repository must use:

        # Copyright YEARS Gentoo Authors
    """

    @property
    def desc(self):
        return f'invalid copyright: {self.line!r}'


class OldGentooCopyright(_FileHeaderResult, Warning):
    """File with old Gentoo Foundation copyright.

    The file still assigns copyright to the Gentoo Foundation even though
    it has been committed after the new copyright policy was approved
    (2018-10-21).

    Ebuilds and eclasses in Gentoo repository must use 'Gentoo Authors'
    instead. Files in other repositories may specify an explicit copyright
    holder instead.
    """

    @property
    def desc(self):
        return f'old copyright, update to "Gentoo Authors": {self.line!r}'


class NonGentooAuthorsCopyright(_FileHeaderResult, Error):
    """File with copyright stating owner other than "Gentoo Authors".

    The file specifies explicit copyright owner, while the Gentoo repository
    policy specifies that all ebuilds and eclasses must use "Gentoo Authors".
    If the owner is not listed in metadata/AUTHORS, addition can be requested
    via bugs.gentoo.org.
    """

    @property
    def desc(self):
        return f'copyright line must state "Gentoo Authors": {self.line!r}'


class InvalidLicenseHeader(_FileHeaderResult, Error):
    """File with invalid license header.

    The file does not have with a valid license header.

    Ebuilds and eclasses in the Gentoo repository must use:

        # Distributed under the terms of the GNU General Public License v2
    """

    @property
    def desc(self):
        return f'invalid license header: {self.line!r}'
