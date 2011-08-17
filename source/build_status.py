#!/usr/bin/python
# -*- coding: UTF-8 -*-

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

# Rewrite of the old build_status script using LP API

# Requirements:
# - python-launchpadlib
# - python-apt
# - python-jinja2

# Uncomment for tracing LP API calls
#import httplib2
#httplib2.debuglevel = 1

import sys
import time
import apt_pkg
from jinja2 import (Environment, FileSystemLoader)
from launchpadlib.errors import HTTPError
from launchpadlib.launchpad import Launchpad
from operator import (attrgetter, methodcaller)

lp_service = 'production'
api_version = 'devel'
default_arch_list = []
find_tagged_bugs = 'ftbfs'
apt_pkg.InitSystem()

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
            except HTTPError, e:
                if e.response.status == 410:
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
        return u'%s (%s)' % (self.display_name, self.name)

class SourcePackage(object):
    _cache = dict()

    class VersionList(list):
        def append(self, item):
            super(SourcePackage.VersionList, self).append(item)
            self.sort(key = attrgetter('version'), cmp = apt_pkg.VersionCompare)

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
            srcpkg.packagesets = set([ps for (ps, srcpkglist) in packagesets.items() if spph.source_package_name in srcpkglist])
            components[spph.component_name].append(srcpkg)
            for ps in srcpkg.packagesets:
                packagesets_ftbfs[ps].append(srcpkg)

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
        def __init__(self, build):
            buildstates = {
                    'Failed to build': 'FAILEDTOBUILD',
                    'Dependency wait': 'MANUALDEPWAIT',
                    'Chroot problem': 'CHROOTWAIT',
                    'Failed to upload': 'UPLOADFAIL',
                    }
            self.buildstate = buildstates[build.buildstate]
            self.url = translate_api_web(build.self_link)

            if self.buildstate == 'UPLOADFAIL':
                self.log = translate_api_web(build.upload_log_url)
            else:
                if build.build_log_url:
                    self.log = translate_api_web(build.build_log_url)
                else:
                    self.log = ''

            if self.buildstate == 'MANUALDEPWAIT':
                self.tooltip = 'waits on %s' % build.dependencies
            elif build.datebuilt is None:
                self.tooltip = 'Broken build'
            else:
                if build.datebuilt:
                    self.tooltip = 'Build finished on %s' % build.datebuilt.strftime('%Y-%m-%d %H:%M:%S UTC')
                else:
                    self.tooltip = 'Build finish unknown'

    def addBuildLog(self, buildlog):
        self.logs[buildlog.arch_tag] = self.BuildLog(buildlog)

    def getArch(self, arch):
        return self.logs.get(arch)

    def getChangedBy(self):
        '''
        Returns a string with the person who changed this package.
        '''
        return u'Changed-By: %s' % (self.changed_by)


def fetch_pkg_list(archive, series, state, arch_list=default_arch_list, main_archive=None, main_series=None):
    print "Processing '%s'" % state

    # XXX wgrant 2009-09-19: This is an awful hack. We should really
    # just let IArchive.getBuildRecords take a series argument.
    if archive.name == 'primary':
        buildlist = series.getBuildRecords(build_state = state)
    else:
        buildlist = archive.getBuildRecords(build_state = state)

    for build in buildlist:
        csp_link = build.current_source_publication_link
        if not csp_link:
            # Build log for an older version
            continue

        if build.arch_tag not in arch_list:
            print "  Skipping %s" % build.title
            continue

        print "  %s" % build.title

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
            else:
                spph.current = True

        if not spph.current:
            print "    superseded"
        SPPH(csp_link).addBuildLog(build)

def generate_page(archive, series, template = 'build_status.html', arch_list = default_arch_list):
    try:
        out = open('../%s-%s.html' % (archive.name, series.name), 'w')
    except IOError:
        return

    # sort the package lists
    filter_ftbfs = lambda pkglist, current: filter(methodcaller('isFTBFS', arch_list, current), sorted(pkglist))
    data = {}
    for comp in ('main', 'restricted', 'universe', 'multiverse'):
        data[comp] = filter_ftbfs(components[comp], True)
        data['%s_superseded' % comp] = filter_ftbfs(components[comp], False)
    for pkgset, pkglist in packagesets_ftbfs.items():
        packagesets_ftbfs[pkgset] = filter_ftbfs(pkglist, True)

    # container object to hold the counts and the tooltip
    class StatData(object):
        def __init__(self, cnt, cnt_superseded, tooltip):
            self.cnt = cnt
            self.cnt_superseded = cnt_superseded
            self.tooltip = tooltip

    # compute some statistics (number of packages for each build failure type)
    stats = {}
    for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL'):
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
                tooltiphtml = u'<table><tr>'
                tooltiphtml += u'</tr><tr>'.join(tooltip)
                tooltiphtml += u'</tr></table>'
                stats[state][arch] = StatData(cnt, cnt_sup, tooltiphtml)
            else:
                stats[state][arch] = StatData(None, None, None)

    data['stats'] = stats
    data['archive'] = archive
    data['series'] = series
    data['arch_list'] = arch_list
    data['lastupdate'] = time.strftime('%F %T %z')
    data['packagesets'] = packagesets_ftbfs

    env = Environment(loader=FileSystemLoader('.'))
    template = env.get_template('build_status.html')
    stream = template.render(**data)
    out.write(stream.encode('utf-8'))
    out.close()

def generate_csvfile(archive, series, arch_list = default_arch_list):
    csvout = open('../%s-%s.csv' % (archive.name, series.name), 'w')
    linetemplate = '%(name)s,%(link)s,%(explain)s\n'
    for comp in components.values():
        for pkg in comp:
            for ver in pkg.versions:
                for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL'):
                    archs = [ arch for (arch, log) in ver.logs.items() if log.buildstate == state ]
                    if archs:
                        log = ver.logs[archs[0]].log
                        csvout.write(linetemplate  % {'name': pkg.name, 'link': log,
                            'explain':"[%s] %s" %(', '.join(archs), state)})

if __name__ == '__main__':
    # login anonymously to LP
    launchpad = Launchpad.login_anonymously('qa-ftbfs', lp_service, version=api_version)

    ubuntu = launchpad.distributions['ubuntu']
    assert len(sys.argv) >= 4

    try:
        archive = ubuntu.getArchive(name=sys.argv[1])
    except HTTPError:
        print 'Error: %s is not a valid archive.' % sys.argv[1]
    try:
        series = ubuntu.getSeries(name_or_version=sys.argv[2])
    except HTTPError:
            print 'Error: %s is not a valid series.' % sys.argv[2]

    if archive.name != 'primary':
        main_archive = ubuntu.main_archive
        main_series = ubuntu.current_series

    default_arch_list.extend(sys.argv[3:])

    for (archive, series) in [(archive, series)]:
        print "Generating FTBFS for %s" % series.fullseriesname

        # clear all caches
        PersonTeam.clear()
        SourcePackage.clear()
        SPPH.clear()

        # list of SourcePackages for each component
        components = {
                'main': [],
                'restricted': [],
                'universe': [],
                'multiverse': [],
                }

        # packagesets for this series
        packagesets = dict()
        packagesets_ftbfs = dict()
        for ps in launchpad.packagesets:
            if ps.distroseries_link == series.self_link:
                packagesets[ps.name] = ps.getSourcesIncluded(direct_inclusion=False)
                packagesets_ftbfs[ps.name] = [] # empty list to add FTBFS for each package set later

        for state in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload'):
            fetch_pkg_list(archive, series, state, default_arch_list, main_archive, main_series)

        print "Generating HTML page..."
        generate_page(archive, series)
        print "Generating CSV file..."
        generate_csvfile(archive, series)
