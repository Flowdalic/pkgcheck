"""Core classes and interfaces.

This defines a couple of standard feed types and scopes. Currently
feed types are strings and scopes are integers, but you should use the
symbolic names wherever possible (everywhere except for adding a new
feed type) since this might change in the future. Scopes are integers,
but do not rely on that either.

Feed types have to match exactly. Scopes are ordered: they define a
minimally accepted scope, and for transforms the output scope is
identical to the input scope.
"""

from collections import OrderedDict, namedtuple
from operator import attrgetter

from pkgcore import const
from pkgcore.config import ConfigHint
from pkgcore.package.errors import MetadataException
from snakeoil.decorators import coroutine
from snakeoil.demandload import demandload
from snakeoil.osutils import pjoin

demandload(
    're',
    'pkgcore.log:logger',
)

# source feed types
repository_feed = "repo"
category_feed = "cat"
package_feed = "cat/pkg"
versioned_feed = "cat/pkg-ver"
ebuild_feed = "cat/pkg-ver+text"

# repo filter types
no_filter = 'none'
mask_filter = 'mask'
git_filter = 'git'

# mapping for -S/--scopes option, ordered for sorted output in the case of unknown scopes
_Scope = namedtuple("Scope", ["threshold", "desc"])
known_scopes = OrderedDict((
    ('repo', _Scope(repository_feed, 'repository')),
    ('cat', _Scope(category_feed, 'category')),
    ('pkg', _Scope(package_feed, 'package')),
    ('ver', _Scope(versioned_feed, 'version')),
))

# The plugger needs to be able to compare those and know the highest one.
version_scope, package_scope, category_scope, repository_scope = list(range(len(known_scopes)))
max_scope = repository_scope

CACHE_DIR = pjoin(const.USER_CACHE_PATH, 'pkgcheck')


class Addon(object):
    """Base class for extra functionality for pkgcheck other than a check.

    The checkers can depend on one or more of these. They will get
    called at various points where they can extend pkgcheck (if any
    active checks depend on the addon).

    These methods are not part of the checker interface because that
    would mean addon functionality shared by checkers would run twice.
    They are not plugins because they do not do anything useful if no
    checker depending on them is active.

    This interface is not finished. Expect it to grow more methods
    (but if not overridden they will be no-ops).

    :cvar required_addons: sequence of addons this one depends on.
    """

    required_addons = ()

    def __init__(self, options, *args):
        """Initialize.

        An instance of every addon in required_addons is passed as extra arg.

        :param options: the argparse values.
        """
        self.options = options

    @staticmethod
    def mangle_argparser(parser):
        """Add extra options and/or groups to the argparser.

        This hook is always triggered, even if the checker is not
        activated (because it runs before the commandline is parsed).

        :param parser: an C{argparse.ArgumentParser} instance.
        """

    @staticmethod
    def check_args(parser, namespace):
        """Postprocess the argparse values.

        Should raise C{argparse.ArgumentError} on failure.

        This is only called for addons that are enabled, but before
        they are instantiated.
        """


class Template(Addon):
    """Base template for a check.

    :cvar scope: scope relative to the package repository the check runs under
    :cvar priority: priority level of the check which plugger sorts by --
        should be left alone except for weird pseudo-checks like the cache
        wiper that influence other checks
    :cvar filter_type: filtering of feed items (by default there are no filters)
    :cvar known_results: result keywords the check can possibly yield
    """

    scope = version_scope
    priority = 0
    filter_type = no_filter
    known_results = ()

    @classmethod
    def skip(cls, namespace):
        """Conditionally skip check when running all enabled checks."""
        return False

    def start(self):
        """Do startup here."""

    def feed(self, item):
        raise NotImplementedError

    def finish(self):
        """Do cleanup and omit final results here."""


class GentooRepoCheck(Template):
    """Check that is only valid when run against the gentoo repo."""

    @classmethod
    def skip(cls, namespace):
        skip = namespace.target_repo.repo_id != 'gentoo'
        if skip:
            logger.info(f'skipping {cls.__name__}, not running against gentoo repo')
        return skip or super().skip(namespace)


class OverlayRepoCheck(Template):
    """Check that is only valid when run against an overlay repo."""

    @classmethod
    def skip(cls, namespace):
        skip = not namespace.target_repo.masters
        if skip:
            logger.info(f'skipping {cls.__name__}, not running against overlay repo')
        return skip or super().skip(namespace)


class ExplicitlyEnabledCheck(Template):
    """Check that is only valid when explicitly enabled."""

    @classmethod
    def skip(cls, namespace):
        if namespace.selected_checks is not None:
            disabled, enabled = namespace.selected_checks
        else:
            disabled, enabled = (), ()
        skip = cls.__name__ not in enabled
        if skip:
            logger.info(f'skipping {cls.__name__}, not explicitly enabled')
        return skip or super().skip(namespace)


class GenericSource(object):
    """Base template for a repository source."""

    feed_type = versioned_feed
    filter_type = no_filter
    required_addons = ()
    cost = 10

    def __init__(self, options, limiter):
        self.options = options
        self.repo = options.target_repo
        self.limiter = limiter


class Transform(object):
    """Base class for a feed type transformer.

    :cvar source: start type
    :cvar dest: destination type
    :cvar scope: minimum scope
    :cvar cost: cost
    """

    def __init__(self, child):
        self.child = child

    def start(self):
        """Startup."""
        yield from self.child.start()

    def feed(self, item):
        raise NotImplementedError

    def finish(self):
        """Clean up."""
        yield from self.child.finish()

    def __repr__(self):
        return f'{self.__class__.__name__}({self.child!r})'


class Result(object):

    __slots__ = ()

    # level values match those used in logging module
    _level = 20
    _level_to_desc = {
        40: ('error', 'red'),
        30: ('warning', 'yellow'),
        20: ('info', 'green'),
    }

    @property
    def color(self):
        return self._level_to_desc[self._level][1]

    @property
    def level(self):
        return self._level_to_desc[self._level][0]

    def __str__(self):
        try:
            return self.desc
        except NotImplementedError:
            return f"result from {self.__class__.__name__}"

    @property
    def desc(self):
        if getattr(self, '_verbosity', False):
            return self.long_desc
        return self.short_desc

    @property
    def short_desc(self):
        raise NotImplementedError

    @property
    def long_desc(self):
        return self.short_desc

    def _store_cp(self, pkg):
        self.category = pkg.category
        self.package = pkg.package

    def _store_cpv(self, pkg):
        self._store_cp(pkg)
        self.version = pkg.fullver

    def __getstate__(self):
        attrs = getattr(self, '__attrs__', getattr(self, '__slots__', None))
        if attrs:
            try:
                return dict((k, getattr(self, k)) for k in attrs)
            except AttributeError as a:
                # rethrow so we at least know the class
                raise AttributeError(self.__class__, str(a))
        return object.__getstate__(self)

    def __setstate__(self, data):
        attrs = set(getattr(self, '__attrs__', getattr(self, '__slots__', [])))
        if attrs.difference(data) or len(attrs) != len(data):
            raise TypeError(
                f"can't restore {self.__class__} due to data {data!r} not being complete")
        for k, v in data.items():
            setattr(self, k, v)


class Error(Result):
    """Result with an error priority level."""
    _level = 40


class Warning(Result):
    """Result with a warning priority level."""
    _level = 30


class LogError(Error):
    """Error caught from a logger instance."""

    __slots__ = ("msg",)

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    @property
    def short_desc(self):
        return self.msg


class LogWarning(Warning, LogError):
    """Warning caught from a logger instance."""


class MetadataError(Error):
    """Problem detected with a package's metadata."""

    __slots__ = ("category", "package", "version", "attr", "msg")
    threshold = versioned_feed

    def __init__(self, pkg, attr, msg):
        super().__init__()
        self._store_cpv(pkg)
        self.attr, self.msg = attr, str(msg)

    @property
    def short_desc(self):
        return f"attr({self.attr}): {self.msg}"


class Reporter(object):
    """Generic result reporter."""

    def __init__(self, out, keywords=None, verbosity=None):
        """Initialize

        :type out: L{snakeoil.formatters.Formatter}
        :param keywords: result keywords to report, other keywords will be skipped
        """
        self.out = out
        self.verbosity = verbosity if verbosity is not None else 0
        self._filtered_keywords = set(keywords) if keywords is not None else keywords

        # initialize result processing coroutines
        self.report = self._add_report().send
        self.process = self._process_report().send

    @coroutine
    def _add_report(self):
        """Add a report result to be processed for output."""
        # only process reports for keywords that are enabled
        while True:
            result = (yield)
            if self._filtered_keywords is None or result.__class__ in self._filtered_keywords:
                result._verbosity = self.verbosity
                self.process(result)

    @coroutine
    def _process_report(self):
        """Render and output a report result.."""
        raise NotImplementedError(self._process_report)

    def start(self):
        """Initialize reporter output."""

    def finish(self):
        """Finalize reporter output."""


def convert_check_filter(tok):
    """Convert an input string into a filter function.

    The filter function accepts a qualified python identifier string
    and returns a bool.

    The input can be a regexp or a simple string. A simple string must
    match a component of the qualified name exactly. A regexp is
    matched against the entire qualified name.

    Matches are case-insensitive.

    Examples::

      convert_check_filter('foo')('a.foo.b') == True
      convert_check_filter('foo')('a.foobar') == False
      convert_check_filter('foo.*')('a.foobar') == False
      convert_check_filter('foo.*')('foobar') == True
    """
    tok = tok.lower()
    if '+' in tok or '*' in tok:
        return re.compile(tok, re.I).match
    else:
        toklist = tok.split('.')

        def func(name):
            chunks = name.lower().split('.')
            if len(toklist) > len(chunks):
                return False
            for i in range(len(chunks)):
                if chunks[i:i + len(toklist)] == toklist:
                    return True
            return False

        return func


class _CheckSet(object):
    """Run only listed checks."""

    # No config hint here since this one is abstract.

    def __init__(self, patterns):
        self.patterns = list(convert_check_filter(pat) for pat in patterns)


class Whitelist(_CheckSet):
    """Only run checks matching one of the provided patterns."""

    pkgcore_config_type = ConfigHint(
        {'patterns': 'list'}, typename='pkgcheck_checkset')

    def filter(self, checks):
        return list(
            c for c in checks
            if any(p(f'{c.__module__}.{c.__name__}') for p in self.patterns))


class Blacklist(_CheckSet):
    """Only run checks not matching any of the provided patterns."""

    pkgcore_config_type = ConfigHint(
        {'patterns': 'list'}, typename='pkgcheck_checkset')

    def filter(self, checks):
        return list(
            c for c in checks
            if not any(p(f'{c.__module__}.{c.__name__}') for p in self.patterns))


def filter_update(objs, enabled=(), disabled=()):
    """Filter a given list of check or result types."""
    if enabled:
        whitelist = Whitelist(enabled)
        objs = list(whitelist.filter(objs))
    if disabled:
        blacklist = Blacklist(disabled)
        objs = list(blacklist.filter(objs))
    return objs


class Scope(object):
    """Only run checks matching any of the given scopes."""

    pkgcore_config_type = ConfigHint(
        {'scopes': 'list'}, typename='pkgcheck_checkset')

    def __init__(self, scopes):
        self.scopes = tuple(int(x) for x in scopes)

    def filter(self, checks):
        return list(c for c in checks if c.scope in self.scopes)


class CheckRunner(object):

    def __init__(self, checks):
        self.checks = checks
        self._metadata_errors = set()

    def start(self):
        for check in self.checks:
            try:
                reports = check.start()
                if reports is not None:
                    yield from reports
            except MetadataException as e:
                exc_info = (e.pkg, e.error)
                # only report distinct metadata errors
                if exc_info not in self._metadata_errors:
                    self._metadata_errors.add(exc_info)
                    error_str = ': '.join(str(e.error).split('\n'))
                    yield MetadataError(e.pkg, e.attr, error_str)

    def feed(self, item):
        for check in self.checks:
            try:
                reports = check.feed(item)
                if reports is not None:
                    yield from reports
            except MetadataException as e:
                exc_info = (e.pkg, e.error)
                # only report distinct metadata errors
                if exc_info not in self._metadata_errors:
                    self._metadata_errors.add(exc_info)
                    error_str = ': '.join(str(e.error).split('\n'))
                    yield MetadataError(e.pkg, e.attr, error_str)

    def finish(self):
        for check in self.checks:
            reports = check.finish()
            if reports is not None:
                yield from reports

    # The plugger tests use these.
    def __eq__(self, other):
        return (self.__class__ is other.__class__ and
            frozenset(self.checks) == frozenset(other.checks))

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(frozenset(self.checks))

    def __repr__(self):
        checks = ', '.join(sorted(str(check) for check in self.checks))
        return f'{self.__class__.__name__}({checks})'


def plug(sinks, transforms, sources, debug=None):
    """Plug together a pipeline.

    This tries to return a single pipeline if possible (even if it is
    more "expensive" than using separate pipelines). If more than one
    pipeline is needed it does not try to minimize the number.

    :param sinks: Sequence of check instances.
    :param transforms: Sequence of transform classes.
    :param sources: Sequence of source instances.
    :param debug: A logging function or C{None}.
    :return: a sequence of sinks that are unreachable (out of scope or
        missing sources/transforms of the right type),
        a sequence of (source, consumer) tuples.
    """

    # This is not optimized to deal with huge numbers of sinks,
    # sources and transforms, but that should not matter (although it
    # may be necessary to handle a lot of sinks a bit better at some
    # point, which should be fairly easy since we only care about
    # their type and scope).

    feed_to_transforms = {}
    for transform in transforms:
        feed_to_transforms.setdefault(transform.source, []).append(transform)

    # Map from typename to best scope
    best_scope = {}
    for source in sources:
        # (not particularly clever, if we get a ton of sources this
        # should be optimized to do less duplicate work).
        reachable = set()
        todo = set([source.feed_type])
        while todo:
            feed_type = todo.pop()
            reachable.add(feed_type)
            for transform in feed_to_transforms.get(feed_type, ()):
                if (transform.scope <= source.scope and transform.dest not in reachable):
                    todo.add(transform.dest)
        for feed_type in reachable:
            scope = best_scope.get(feed_type)
            if scope is None or scope < source.scope:
                best_scope[feed_type] = source.scope

    # Throw out unreachable sinks.
    good_sinks = []
    bad_sinks = []
    for sink in sinks:
        scope = best_scope.get(sink.feed_type)
        if scope is None or sink.scope > scope:
            bad_sinks.append(sink)
        else:
            good_sinks.append(sink)

    if not good_sinks:
        # No point in continuing.
        return bad_sinks, ()

    # Throw out all sources with a scope lower than the least required scope.
    # Does not check transform requirements, may not be very useful.
    lowest_required_scope = min(sink.scope for sink in good_sinks)
    highest_required_scope = max(sink.scope for sink in good_sinks)
    sources = list(s for s in sources if s.scope >= lowest_required_scope)
    if not sources:
        # No usable sources, abort.
        return bad_sinks + good_sinks, ()

    # All types we need to reach.
    sink_feed_types = frozenset(sink.feed_type for sink in good_sinks)
    sink_filter_types = frozenset(sink.filter_type for sink in good_sinks)

    # Map from (scope, source typename, source filter typename) to cheapest source.
    source_map = {}
    for source in sources:
        current_source = source_map.get((source.scope, source.feed_type))
        if current_source is None or current_source.cost > source.cost:
            source_map[source.scope, source.feed_type, source.filter_type] = source

    # tuples of (visited_types, source, transforms, price)
    pipes = set()
    unprocessed = set(
        (frozenset((source.feed_type,)), source, frozenset(), source.cost)
        for source in source_map.values())
    if debug is not None:
        for pipe in unprocessed:
            debug(f'initial: {pipe!r}')

    # If we find a single pipeline driving all sinks we want to use it.
    # List of tuples of source, transforms.
    pipes_to_run = []
    best_cost = None
    required_filters = set(sink_filter_types)
    required_filters_costs = {}
    while unprocessed:
        pipe = unprocessed.pop()
        if pipe in pipes:
            continue
        pipes.add(pipe)
        visited, source, trans, cost = pipe
        best_cost = required_filters_costs.get(source.filter_type, None)
        if visited >= sink_feed_types and source.filter_type in required_filters:
            required_filters.discard(source.filter_type)
            # Already reaches all sink types. Check if it is usable as
            # single pipeline:
            # if best_cost is None or cost < best_cost:
            if best_cost is None or cost < best_cost:
                pipes_to_run.append((source, trans))
                required_filters_costs[source.filter_type] = cost
                best_cost = cost
            # No point in growing this further: it already reaches everything.
            continue
        if not required_filters and (best_cost is not None and best_cost <= cost):
            # No point in growing this further.
            continue
        for transform in transforms:
            if (source.scope >= transform.scope and
                    transform.source in visited and
                    transform.dest not in visited):
                unprocessed.add((
                    visited.union((transform.dest,)), source,
                    trans.union((transform,)), cost + transform.cost))
                if debug is not None:
                    debug(f'growing {trans!r} for {source!r} with {transform!r}')

    if not pipes_to_run:
        # No single pipe will drive everything, try combining pipes.
        # This is pretty stupid but effective. Map sources to
        # pipelines they drive, try combinations of sources (using a
        # source more than once in a combination makes no sense since
        # we also have the "combined" pipeline in pipes).
        source_to_pipes = {}
        for visited, source, trans, cost in pipes:
            source_to_pipes.setdefault(source, []).append(
                (visited, trans, cost))
        unprocessed = set(
            (visited, frozenset([source]), ((source, trans),), cost)
            for visited, source, trans, cost in pipes)
        done = set()
        while unprocessed:
            pipe = unprocessed.pop()
            if pipe in done:
                continue
            done.add(pipe)
            visited, sources, seq, cost = pipe
            if visited >= sink_feed_types:
                # This combination reaches everything.
                if best_cost is None or cost < best_cost:
                    pipes_to_run = seq
                    best_cost = cost
                # No point in growing this further.
            if best_cost is not None and best_cost <= cost:
                # No point in growing this further.
                continue
            for source, source_pipes in source_to_pipes.items():
                if source not in sources:
                    for new_visited, trans, new_cost in source_pipes:
                        unprocessed.add((
                            visited.union(new_visited),
                            sources.union([source]),
                            seq + ((source, trans),),
                            cost + new_cost))

    # Just an assert since unreachable sinks should have been thrown away.
    assert pipes_to_run, 'did not find a solution?'

    good_sinks.sort(key=attrgetter('priority'))

    def build_transform(scope, feed_type, filter_type, transforms):
        children = []
        for transform in transforms:
            if transform.source == feed_type and transform.scope <= scope:
                # Note this relies on the cheapest pipe not having any "loops"
                # in its transforms.
                t = build_transform(scope, transform.dest, filter_type, transforms)
                if t:
                    children.append(transform(t))
        # Hacky: we modify this in place.
        for i in reversed(range(len(good_sinks))):
            sink = good_sinks[i]
            if (sink.feed_type == feed_type and
                    sink.filter_type == filter_type and sink.scope <= scope):
                children.append(sink)
                del good_sinks[i]
        if children:
            return CheckRunner(children)

    result = []
    for source, transforms in pipes_to_run:
        transform = build_transform(
            source.scope, source.feed_type, source.filter_type, transforms)
        if transform:
            result.append((source, transform))

    assert not good_sinks, f'sinks left: {good_sinks!r}'
    return bad_sinks, result
