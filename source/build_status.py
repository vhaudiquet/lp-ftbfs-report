#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

# Rewrite of the old build_status script using LP API

# Requirements:
# - python3-debian
# - python3-jinja2
# - python3-launchpadlib
# - python3-requests

# Uncomment for tracing LP API calls
#import httplib2
#httplib2.debuglevel = 1

import os
import requests
import sys
import time
import functools
#import apt_pkg
import debian.debian_support
import json
from datetime import datetime
from jinja2 import (Environment, FileSystemLoader)
from launchpadlib.errors import HTTPError
from launchpadlib.launchpad import Launchpad
from operator import (attrgetter, methodcaller)
from optparse import OptionParser

lp_service = 'production'
api_version = 'devel'
default_arch_list = []
find_tagged_bugs = 'ftbfs'
#apt_pkg.init_system()

# copied from ubuntu-dev-tools, libsupport.py:
def translate_api_web(self_url):
    if self_url is None:
        return ''
    else:
        return self_url.replace('api.', '').replace('%s/' % api_version, '')

class PersonTeam(object):
    _cache = dict()

    def __new__(cls, personteam_link):
        try:
            return cls._cache[personteam_link]
        except KeyError:
            try:
                personteam = super(PersonTeam, cls).__new__(cls)

                # fill the new PersonTeam object with data
                lp_object = launchpad.load(personteam_link)
                personteam.display_name = lp_object.display_name
                personteam.name = lp_object.name

            except KeyError:
                return None
            except HTTPError as e:
                if e.response.status in (404, 410):
                    personteam = None
                else:
                    raise

            # add to cache
            cls._cache[personteam_link] = personteam

            return personteam

    @classmethod
    def clear(cls):
        cls._cache.clear()

    def __str__(self):
        return '%s (%s)' % (self.display_name, self.name)

class SourcePackage(object):
    _cache = dict()

    class VersionList(list):
        def append(self, item):
            super(SourcePackage.VersionList, self).append(item)
            #self.sort(key = attrgetter('version'), cmp = apt_pkg.version_compare)
            #self.sort(key = attrgetter('version'))
            self.sort(key = lambda x: debian.debian_support.Version(x.version))
            
            #self.sort(key = functools.cmp_to_key(apt_pkg.version_compare))
            # TypeError: a bytes-like object is required, not 'SPPH'

    def __new__(cls, spph):
        try:
            return cls._cache[spph.source_package_name]
        except KeyError:
            srcpkg = super(SourcePackage, cls).__new__(cls)

            # fill the new SourcePackage object with data
            srcpkg.name = spph.source_package_name
            srcpkg.url = 'https://launchpad.net/ubuntu/+source/%s' % srcpkg.name
            srcpkg.versions = cls.VersionList()
            if find_tagged_bugs is None:
                srcpkg.tagged_bugs = []
            else:
                ts = ubuntu.getSourcePackage(name=srcpkg.name).searchTasks(tags=find_tagged_bugs)
                srcpkg.tagged_bugs = [t.bug for t in ts]
            srcpkg.packagesets = set([ps for (ps, srcpkglist) in list(packagesets.items()) if spph.source_package_name in srcpkglist])
            components[spph.component_name].append(srcpkg)
            for ps in srcpkg.packagesets:
                packagesets_ftbfs[ps].append(srcpkg)

            srcpkg.teams = set([team for (team, srcpkglist) in list(teams.items()) if spph.source_package_name in srcpkglist and spph.component_name == "main"])
            for team in srcpkg.teams:
                teams_ftbfs[team].append(srcpkg)

            # add to cache
            cls._cache[spph.source_package_name] = srcpkg

            return srcpkg

    @classmethod
    def clear(cls):
        cls._cache.clear()

    def __cmp__(self, other):
        return cmp(self.name, other.name)

    def isFTBFS(self, arch_list = default_arch_list, current = True):
        ''' Returns True if at least one FTBFS exists. '''
        for ver in self.versions:
            if ver.current != current:
                continue
            for arch in arch_list:
                log = ver.getArch(arch)
                if log is not None:
                    return True
        return False

    def getCount(self, arch, state):
        count = 0
        for ver in self.versions:
            if arch in ver.logs and ver.logs[arch].buildstate == state:
                count += 1
        return count

    def getPackagesets(self, name=None):
        '''Return the list of packagesets without the packageset `name`.'''
        if name is None:
            return list(self.packagesets)
        else:
            return list(self.packagesets.difference((name,)))

class MainArchiveBuilds(object):
    _cache = dict()

    def __new__(cls, main_archive, source, version):
        try:
            return cls._cache["%s,%s" % (source, version)]
        except KeyError:
            bfm = super(MainArchiveBuilds, cls).__new__(cls)
            results = {}
            sourcepubs = main_archive.getPublishedSources(
                exact_match=True, source_name=source, version=version)
            for pub in sourcepubs:
                for build in pub.getBuilds():
                    # assumes sourcepubs are sorted latest release to oldest,
                    # so first record wins
                    if build.arch_tag not in results:
                        results[build.arch_tag] = build.buildstate
            bfm.results = results
            # add to cache
            cls._cache["%s,%s" % (source, version)] = bfm

            return bfm

    @classmethod
    def clear(cls):
        cls._cache.clear()

class SPPH(object):
    _cache = dict() # dict with all SPPH objects

    def __new__(cls, spph_link):
        try:
            return cls._cache[spph_link]
        except KeyError:
            spph = super(SPPH, cls).__new__(cls)

            # fill the new SPPH object with data
            lp_object = launchpad.load(spph_link)
            spph._lp = lp_object
            spph.logs = dict()
            spph.version = lp_object.source_package_version
            spph.pocket = lp_object.pocket
            spph.changed_by = PersonTeam(lp_object.package_creator_link)
            #spph.signed_by = spph._lp.package_signer_link and PersonTeam(lp_object.package_signer_link)
            spph.current = None
            SourcePackage(lp_object).versions.append(spph)

            # add to cache
            cls._cache[spph_link] = spph

            return spph

    @classmethod
    def clear(cls):
        cls._cache.clear()

    class BuildLog(object):
        def __init__(self, build, never_built, no_regression):
            buildstates = {
                    'Failed to build': 'FAILEDTOBUILD',
                    'Dependency wait': 'MANUALDEPWAIT',
                    'Chroot problem': 'CHROOTWAIT',
                    'Failed to upload': 'UPLOADFAIL',
                    'Cancelled build': 'CANCELLED',
                    'Always FTBFS': 'ALWAYSFTBFS',
                    'Always DepWait': 'ALWAYSDEPWAIT',
                    'NoRegr FTBFS': 'NOREGRFTBFS',
                    'NoRegr DepWait': 'NOREGRDEPWAIT',
                    }
            self.buildstate = buildstates[build.buildstate]
            if no_regression and self.buildstate == 'FAILEDTOBUILD':
                self.buildstate = 'NOREGRFTBFS'
            elif no_regression and self.buildstate == 'MANUALDEPWAIT':
                self.buildstate = 'NOREGRDEPWAIT'

            # overriding regression status with never_built status
            if never_built and self.buildstate == 'FAILEDTOBUILD':
                self.buildstate = 'ALWAYSFTBFS'
            elif never_built and self.buildstate == 'MANUALDEPWAIT':
                self.buildstate = 'ALWAYSDEPWAIT'
            self.url = translate_api_web(build.self_link)

            if self.buildstate == 'UPLOADFAIL':
                self.log = translate_api_web(build.upload_log_url)
            else:
                if build.build_log_url:
                    self.log = translate_api_web(build.build_log_url)
                else:
                    self.log = ''

            if self.buildstate in ('MANUALDEPWAIT', 'ALWAYSDEPWAIT', 'NOREGRDEPWAIT'):
                self.tooltip = 'waits on %s' % build.dependencies
            elif build.datebuilt is None:
                self.tooltip = 'Broken build'
            else:
                if build.datebuilt:
                    self.tooltip = 'Build finished on %s' % build.datebuilt.strftime('%Y-%m-%d %H:%M:%S UTC')
                else:
                    self.tooltip = 'Build finish unknown'

    def addBuildLog(self, buildlog, never_built, no_regression):
        self.logs[buildlog.arch_tag] = self.BuildLog(buildlog, never_built, no_regression)

    def getArch(self, arch):
        return self.logs.get(arch)

    def getChangedBy(self):
        '''
        Returns a string with the person who changed this package.
        '''
        return 'Changed-By: %s' % (self.changed_by)


# cache: (source_package_name, arch_tag) -> build
update_builds = {}

def fetch_pkg_list(archive, series, state, last_published, arch_list=default_arch_list, main_archive=None, main_series=None, release_only=False, is_updates_archive=False, regressions_only=False, ref_series=None):
    print("Processing '%s'" % state)
    if last_published:
        last_published = last_published.replace(tzinfo=None)

    cur_last_published = None
    # XXX wgrant 2009-09-19: This is an awful hack. We should really
    # just let IArchive.getBuildRecords take a series argument.
    if archive.name == 'primary':
        buildlist = series.getBuildRecords(build_state = state)
    else:
        buildlist = archive.getBuildRecords(build_state = state)

    for build in buildlist:
        if (last_published is not None and
            build.datebuilt is not None and
            last_published > build.datebuilt.replace(tzinfo=None)):
                # leave the loop as we're past the last known published build record
                break

        csp_link = build.current_source_publication_link
        if not csp_link:
            # Build log for an older version
            continue

        if build.arch_tag not in arch_list:
            print("  Skipping %s" % build.title)
            continue

        cur_last_published = build.datebuilt

        print("  %s %s" % (build.datebuilt, build.title))

        if is_updates_archive:
            if state == 'Successfully built':
                update_builds[(build.source_package_name, build.arch_tag)] = build
                continue
        else:
            if (build.source_package_name, build.arch_tag) in update_builds:
                print('    Skipping %s, build succeeded in updates-archive' % build.source_package_name)
                continue
            
        spph = SPPH(csp_link)

        if spph.current is None:
            # If a main archive is specified, we check if the current source
            # is still published there. If it isn't, then it's out of date.
            # We should make this obvious.
            # The main archive will normally be the primary archive, and
            # probably only makes sense if the target archive is a rebuild.
            if main_archive:
                main_publications = main_archive.getPublishedSources(
                    distro_series=main_series,
                    exact_match=True,
                    source_name=spph._lp.source_package_name,
                    version=spph._lp.source_package_version,
                    status='Published')
                spph.current = len(main_publications[:1]) > 0
            elif release_only:
                release_publications = archive.getPublishedSources(
                    distro_series=series,
                    pocket='Release',
                    exact_match=True,
                    source_name=spph._lp.source_package_name,
                    version=spph._lp.source_package_version,
                    status='Published')
                spph.current = len(release_publications[:1]) > 0
                if not spph.current:
                    release_publications = archive.getPublishedSources(
                        distro_series=series,
                        pocket='Release',
                        exact_match=True,
                        source_name=spph._lp.source_package_name,
                        version=spph._lp.source_package_version,
                        status='Pending')
                    spph.current = len(release_publications[:1]) > 0
            else:
                spph.current = True

        if not spph.current:
            print("    superseded")

        no_regression = False
        if main_archive:
            # If this build failure is not a regression versus the
            # main archive, do not report it.
            main_builds = MainArchiveBuilds(main_archive,
                                            spph._lp.source_package_name,
                                            spph._lp.source_package_version)
            try:
                if main_builds.results[arch] != 'Successfully built':
                    if regressions_only:
                        print("  Skipping %s" % build.title)
                        continue
                    else:
                        no_regression = True
            except KeyError:
                pass

        # set a never_built status
        already_built = False
        if ref_series:
            if main_archive:
                # test rebuild archive
                reference_archive = main_archive
                # search for successful build in ref_series
                ref_build = get_reference_build(reference_archive, ref_series, ['Updates', 'Release'], build, arch_list)
                if ref_build:
                    already_built = True
                else:
                    # search for successful build in series
                    ref_build = get_reference_build(reference_archive, series, ['Release'], build, arch_list)
                    if ref_build:
                        already_built = True
                    else:
                        # search for successful build in Updates pocket
                        ref_build = get_reference_build(reference_archive, series, ['Updates'], build, arch_list)
                        if ref_build:
                            already_built = True
            else:
                # primary archive
                reference_archive = archive
                # search for successful build in ref_series
                ref_build = get_reference_build(reference_archive, ref_series, ['Updates', 'Release'], build, arch_list)
                if ref_build:
                    already_built = True
                else:
                    # search for successful build in Release pocket
                    ref_build = get_reference_build(reference_archive, series, ['Release'], build, arch_list)
                    if spph.pocket == 'Proposed' and ref_build:
                        already_built = True
                    else:
                        # search for successful build in Updates pocket, XXX same as above?
                        ref_build = get_reference_build(reference_archive, series, ['Updates'], build, arch_list)
                        if spph.pocket == 'Proposed' and ref_build:
                            already_built = True
                    # no reason to look for spph.pocket == 'Proposed'
        never_built = not already_built
        if never_built:
            print('    never built before')

        SPPH(csp_link).addBuildLog(build, never_built, no_regression)

    return cur_last_published

# cache: (source_package_name, series, pocket, arch_tag) -> build
reference_builds = {}

def get_reference_build(archive, series, pockets, build, arch_list):
    """find a successful build in archive/series/pockets with build.arch_tag and build.source_package_name"""

    print("    Find reference build: %s / %s / %s / %s" % (build.source_package_name, build.arch_tag, pockets, series.name))
    # cache lookup
    br = None
    for pocket in pockets:
        br = reference_builds.get((build.source_package_name, series.name, pocket, build.arch_tag), None)
        if br:
            print('        cache :', br.source_package_name, br.arch_tag)
            return br

    if len(pockets) == 1:
        ref_sources = archive.getPublishedSources(
            source_name=build.source_package_name,
            exact_match=True,
            distro_series=series,
            status='Published',
            pocket=pockets[0]
        )
    else:
        ref_sources = archive.getPublishedSources(
            source_name=build.source_package_name,
            exact_match=True,
            distro_series=series,
            status='Published'
        )
    found = None
    for rs in ref_sources:
        if rs.pocket not in pockets:
            continue
        print('      v=%s, %s' % (rs.source_package_version, rs.pocket))
        # getBuilds() doesn't find anything when a package was not (re)built in a series
        binaries = rs.getPublishedBinaries()
        for b in binaries:
            if b.is_debug:
                continue
            if b.pocket not in pockets:
                continue
            b_arch = b.distro_arch_series_link.split('/')[-1]
            if b_arch not in arch_list:
                continue

            # get the build, state is 'Successfully built'
            br = reference_builds.get((build.source_package_name, series.name, b.pocket, b_arch), None)
            if not br:
                br = b.build

            #print '          cand:', br.source_package_name, br.arch_tag, br.buildstate
            # cache br for any architecture in arch_list
            reference_builds[(build.source_package_name, series.name, b.pocket, b_arch)] = br

            if build.arch_tag == br.arch_tag:
                found = br
            # continue, so we don't call getPublishedSources/getPublishedBinaries for other archs again
            # break
        # only interested in the most recent published source
        break
    if found:
        print('        found:', br.source_package_name, br.arch_tag)
    return found

def generate_page(name, archive, updates_archive, series, archs_by_archive, main_archive, template = 'build_status.html', arch_list = default_arch_list, notice=None, release_only=False, regressions_only=False, ref_series=None, generated=''):
    # sort the package lists
    filter_ftbfs = lambda pkglist, current: list(filter(methodcaller('isFTBFS', arch_list, current),
                                                        sorted(pkglist, key=lambda src: src.name)))
    data = {}
    for comp in ('main', 'restricted', 'universe', 'multiverse'):
        data[comp] = filter_ftbfs(components[comp], True)
        data['%s_superseded' % comp] = filter_ftbfs(components[comp], False) if not release_only else []
    for pkgset, pkglist in list(packagesets_ftbfs.items()):
        packagesets_ftbfs[pkgset] = filter_ftbfs(pkglist, True)
    for team, pkglist in list(teams_ftbfs.items()):
        teams_ftbfs[team] = filter_ftbfs(pkglist, True)

    # container object to hold the counts and the tooltip
    class StatData(object):
        def __init__(self, cnt, cnt_superseded, tooltip):
            self.cnt = cnt
            self.cnt_superseded = cnt_superseded
            self.tooltip = tooltip

    # compute some statistics (number of packages for each build failure type)
    stats = {}
    for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'CANCELLED', 'ALWAYSFTBFS', 'ALWAYSDEPWAIT', 'NOREGRFTBFS', 'NOREGRDEPWAIT'):
        stats[state] = {}
        for arch in arch_list:
            tooltip = []
            cnt = 0
            cnt_sup = 0
            for comp in ('main', 'restricted', 'universe', 'multiverse'):
                s = sum([pkg.getCount(arch, state) for pkg in data[comp]])
                s_sup = sum([pkg.getCount(arch, state) for pkg in data['%s_superseded' % comp]])
                if s or s_sup:
                    cnt += s
                    cnt_sup += s_sup
                    tooltip.append('<td>%s:</td><td style="text-align:right;">%i (%i superseded)</td>' % (comp, s, s_sup))
            if cnt:
                tooltiphtml = '<table><tr>'
                tooltiphtml += '</tr><tr>'.join(tooltip)
                tooltiphtml += '</tr></table>'
                stats[state][arch] = StatData(cnt, cnt_sup, tooltiphtml)
            else:
                stats[state][arch] = StatData(None, None, None)

    data['stats'] = stats
    data['archive'] = archive
    data['updates_archive'] = updates_archive
    data['main_archive'] = main_archive
    data['series'] = series
    data['arch_list'] = arch_list
    data['archs_by_archive'] = archs_by_archive
    data['lastupdate'] = time.strftime('%F %T %z')
    data['generated'] = generated
    data['packagesets'] = packagesets_ftbfs
    data['teams'] = teams_ftbfs
    data['notice'] = notice
    data['abbrs'] = {
        'FAILEDTOBUILD': 'F',
        'CANCELLED': 'X',
        'MANUALDEPWAIT': 'M',
        'CHROOTWAIT': 'C',
        'UPLOADFAIL': 'U',
        'ALWAYSFTBFS': 'F',
        'ALWAYSDEPWAIT': 'M',
        'NOREGRFTBFS': 'F',
        'NOREGRDEPWAIT': 'M',
        }
    descr = 'Archive: %s' % archive.displayname
    if updates_archive:
        descr += ' / Updates: %s' % updates_archive.displayname
    if ref_series:
        descr += ' / Reference series: %s' % ref_series
    if regressions_only:
        descr += ' / Only report regressions'
    data['description'] = descr

    env = Environment(loader=FileSystemLoader('.'))
    template = env.get_template('build_status.html')
    stream = template.render(**data)

    fn = '../%s.html' % name
    out = open('%s.new' % fn, 'wb')
    out.write(stream.encode('utf-8'))
    out.close()
    os.rename('%s.new' % fn, fn)

def generate_csvfile(name, arch_list = default_arch_list):
    csvout = open('../%s.csv' % name, 'w')
    linetemplate = '%(name)s,%(link)s,%(explain)s\n'
    for comp in list(components.values()):
        for pkg in comp:
            for ver in pkg.versions:
                for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'CANCELLED', 'ALWAYSFTBFS', 'ALWAYSDEPWAIT', 'NOREGRFTBFS', 'NOREGRDEPWAIT'):
                    archs = [ arch for (arch, log) in list(ver.logs.items()) if log.buildstate == state ]
                    if archs:
                        log = ver.logs[archs[0]].log
                        csvout.write(linetemplate  % {'name': pkg.name, 'link': log,
                            'explain':"[%s] %s" %(', '.join(archs), state)})

def load_timestamps(name):
    '''Load the saved timestamps about the last still published FTBFS build record.'''
    try:
        timestamp_file = open('%s.json' % name, 'r')
        tmp = json.load(timestamp_file)
        timestamps = {}
        for state, timestamp in list(tmp.items()):
            try:
                timestamps[state] = datetime.utcfromtimestamp(int(timestamp))
            except TypeError:
                timestamps[state] = None
        return timestamps
    except (IOError):
        return {
            'Successfully built': None,
            'Failed to build': None,
            'Dependency wait': None,
            'Chroot problem': None,
            'Failed to upload': None,
            'Cancelled build': None,
        }

def save_timestamps(name, timestamps):
    '''Save the timestamps of the last still published FTBFS build record into a JSON file.'''
    timestamp_file = open('%s.json' % name, 'w')
    tmp = {}
    for state, timestamp in list(timestamps.items()):
        if timestamp is not None:
            tmp[state] = timestamp.strftime('%s')
        else:
            tmp[state] = None
    json.dump(tmp, timestamp_file)
    timestamp_file.close()

if __name__ == '__main__':
    # login anonymously to LP
    launchpad = Launchpad.login_anonymously('qa-ftbfs', lp_service, version=api_version)

    global ubuntu
    ubuntu = launchpad.distributions['ubuntu']

    usage = "usage: %prog [options] <archive> <series> <arch> [<arch> ...]"
    parser = OptionParser(usage=usage)
    parser.add_option(
        "-f", "--filename", dest="name",
        help="File name prefix for the result.")
    parser.add_option(
        "-n", "--notice", dest="notice_file",
        help="HTML notice file to include in the page header.")
    parser.add_option(
        "--regressions-only", dest="regressions_only", action="store_true", default=False,
        help="Only report build regressions, compared to the main archive.")
    parser.add_option(
        "--release-only", dest="release_only", action="store_true",
        help="Only include sources currently published in the release pocket.")
    parser.add_option(
        "--updates-archive", dest="updates_archive",
        help="Name of an updates archive.")
    parser.add_option(
        "--reference-series", dest="ref_series",
        help="Name of the series to look for successful builds.")
    (options, args) = parser.parse_args()
    if len(args) < 3:
        parser.error("Need at least 3 arguments.")

    try:
        archive = ubuntu.getArchive(name=args[0])
    except HTTPError:
        print('Error: %s is not a valid archive.' % args[0])

    if options.updates_archive:
        try:
            updates_archive = ubuntu.getArchive(name=options.updates_archive)
        except HTTPError:
            print('Error: %s is not a valid archive.' % options.updates_archive)
    else:
        updates_archive = None
        print('no updates-archive is used')

    if options.ref_series:
        try:
            ref_series = ubuntu.getSeries(name_or_version=options.ref_series)
        except HTTPError:
            print('Error: %s is not a valid series.' % options.ref_series)
    else:
        ref_series = None
        print('no reference series is used')

    try:
        series = ubuntu.getSeries(name_or_version=args[1])
    except HTTPError:
            print('Error: %s is not a valid series.' % args[1])

    if options.name is None:
        options.name = '%s-%s' % (archive.name, series.name)

    if archive.name != 'primary':
        main_archive = ubuntu.main_archive
        main_series = series
    else:
        main_archive = main_series = None

    archs_by_archive = dict(main=[], ports=[])
    for arch in args[2:]:
        das = series.getDistroArchSeries(archtag=arch)
        archs_by_archive[das.official and 'main' or 'ports'].append(arch)
    default_arch_list.extend(archs_by_archive['main'])
    default_arch_list.extend(archs_by_archive['ports'])

    generated_info = datetime.utcnow().strftime('Started: %Y-%m-%d %X')

    for (archive, series) in [(archive, series)]:
        print("Generating FTBFS for %s" % series.fullseriesname)

        # clear all caches
        PersonTeam.clear()
        SourcePackage.clear()
        SPPH.clear()
        last_published = load_timestamps(options.name)

        # list of SourcePackages for each component
        components = {
                'main': [],
                'restricted': [],
                'universe': [],
                'multiverse': [],
                'partner': [],
                }

        # packagesets for this series
        packagesets = dict()
        packagesets_ftbfs = dict()
        for ps in launchpad.packagesets:
            if ps.distroseries_link == series.self_link:
                packagesets[ps.name] = ps.getSourcesIncluded(direct_inclusion=False)
                packagesets_ftbfs[ps.name] = [] # empty list to add FTBFS for each package set later

        teams = requests.get('https://people.canonical.com/~ubuntu-archive/package-team-mapping.json').json()

        # Per team list of FTBFS
        teams_ftbfs = {team: [] for team in teams}

        if updates_archive:
            print("XXX: processing updates archive ...")
            last_updates_published = {
                'Successfully built': None,
                'Failed to build': None,
                'Dependency wait': None,
                'Chroot problem': None,
                'Failed to upload': None,
                'Cancelled build': None,
            }
            for state in ('Successfully built', 'Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload', 'Cancelled build'):
                last_updates_published[state] = fetch_pkg_list(
                    updates_archive, series, state, last_updates_published[state],
                    default_arch_list, main_archive, main_series, options.release_only,
                    is_updates_archive=True, regressions_only=options.regressions_only, ref_series=ref_series
                )
            
        print("XXX: processing archive ...")
        for state in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload', 'Cancelled build'):
            last_published[state] = fetch_pkg_list(
                archive, series, state, last_published[state],
                default_arch_list, main_archive, main_series, options.release_only,
                regressions_only=options.regressions_only, ref_series=ref_series
            )

        save_timestamps(options.name, last_published)

        if options.notice_file:
            notice = open(options.notice_file).read()
        else:
            notice = None

        generated_info += datetime.utcnow().strftime('  /  Finished: %Y-%m-%d %X')

        print("Generating HTML page...")
        generate_page(options.name, archive, updates_archive, series, archs_by_archive, main_archive,
                      notice=notice, release_only=options.release_only, ref_series=options.ref_series,
                      generated=generated_info)
        print("Generating CSV file...")
        generate_csvfile(options.name)
