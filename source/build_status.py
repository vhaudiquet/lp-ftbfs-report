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
api_version = '1.0'
default_arch_list = ('i386', 'amd64', 'armel', 'powerpc')
apt_pkg.InitSystem()

# list of SourcePackages for each component
components = {
        'main': [],
        'restricted': [],
        'universe': [],
        'multiverse': [],
        }

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
            components[spph.component_name].append(srcpkg)

            # add to cache
            cls._cache[spph.source_package_name] = srcpkg

            return srcpkg

    @classmethod
    def clear(cls):
        cls._cache.clear()

    def __cmp__(self, other):
        return cmp(self.name, other.name)

    def isFTBFS(self, arch_list = default_arch_list):
        ''' Returns True if at least one FTBFS exists. '''
        for ver in self.versions:
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

class SPPH(object):
    _cache = dict() # dict with all SPPH objects

    def __new__(cls, spph_link):
        try:
            return cls._cache[spph_link]
        except KeyError:
            spph = super(SPPH, cls).__new__(cls)

            # fill the new SPPH object with data
            lp_object = launchpad.load(spph_link)
            spph.logs = dict()
            spph.version = lp_object.source_package_version
            spph.pocket = lp_object.pocket
            spph.changed_by = PersonTeam(lp_object.package_creator_link)
            #spph.signed_by = spph._lp.package_signer_link and PersonTeam(lp_object.package_signer_link)
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
                self.log = translate_api_web(build.build_log_url)

            if self.buildstate == 'MANUALDEPWAIT':
                self.tooltip = 'waits on %s' % build.dependencies
            elif build.datebuilt is None:
                self.tooltip = 'Broken build'
            else:
                self.tooltip = 'Build finished on %s' % build.datebuilt.strftime('%Y-%m-%d %H:%M:%S UTC')

    def addBuildLog(self, buildlog):
        self.logs[buildlog.arch_tag] = self.BuildLog(buildlog)

    def getArch(self, arch):
        return self.logs.get(arch)

    def getChangedBy(self):
        '''
        Returns a string with the person who changed this package.
        '''
        return u'Changed-By: %s' % (self.changed_by)

def fetch_pkg_list(series, state, arch_list=default_arch_list):
    print "Processing '%s'" % state

    buildlist = series.getBuildRecords(build_state = state)

    for build in buildlist:
        csp_link = build.current_source_publication_link
        if not csp_link:
            # Build log for an older version
            continue

        if build.arch_tag not in arch_list:
            print "  Skipping %s" % build.title
            continue

        print csp_link

        print "  %s" % build.title

        SPPH(csp_link).addBuildLog(build)

def generate_page(series, template = 'build_status.html', arch_list = default_arch_list):
    try:
        out = open('../%s.html' % series.name, 'w')
    except IOError:
        return

    filter_ftbfs = lambda comp: filter(methodcaller('isFTBFS', arch_list), sorted(components[comp]))
    data = {}
    data['main'] = filter_ftbfs('main')
    data['universe'] = filter_ftbfs('universe')
    data['restricted'] = filter_ftbfs('restricted')
    data['multiverse'] = filter_ftbfs('multiverse')

    # container object to hold the counts and the tooltip
    class StatData(object):
        def __init__(self, cnt, tooltip):
            self.cnt = cnt
            self.tooltip = tooltip

    # compute some statistics (number of packages for each build failure type)
    stats = {}
    for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL'):
        stats[state] = {}
        for arch in arch_list:
            tooltip = []
            cnt = 0
            for comp in ('main', 'restricted', 'universe', 'multiverse'):
                s = sum([pkg.getCount(arch, state) for pkg in data[comp]])
                if s:
                    cnt += s
                    tooltip.append('<td>%s:</td><td style="text-align:right;">%i</td>' % (comp, s))
            if cnt:
                tooltiphtml = u'<table><tr>'
                tooltiphtml += u'</tr><tr>'.join(tooltip)
                tooltiphtml += u'</tr></table>'
                stats[state][arch] = StatData(cnt, tooltiphtml)
            else:
                stats[state][arch] = StatData(None, None)

    data['stats'] = stats
    data['series'] = series
    data['active_series_list'] = active_series_list
    data['arch_list'] = arch_list
    data['lastupdate'] = time.strftime('%F %T %z')

    env = Environment(loader=FileSystemLoader('.'))
    template = env.get_template('build_status.html')
    stream = template.render(**data)
    out.write(stream.encode('utf-8'))
    out.close()

def generate_csvfile(series, arch_list = default_arch_list):
    csvout = open('../%s.csv' % series.name, 'w')
    linetemplate = '%(name)s,%(link)s,%(explain)s\n'
    for comp in components.values():
        for pkg in comp:
            for ver in pkg.versions:
                for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL'):
                    archs = [ arch for (arch, log) in ver.logs.items() if log.buildstate == state ]
                    if archs:
                        log = ver.logs[archs[0]].log
                        csvout.write(linetemplate  % {'name': pkg.name, 'link': log,
                            'explain':"[%s] %s" %(','.join(archs), state)})

if __name__ == '__main__':
    # login anonymously to LP
    launchpad = Launchpad.login_anonymously('qa-ftbfs', lp_service)

    ubuntu = launchpad.distributions['ubuntu']
    active_series_list = sorted([s for s in ubuntu.series if s.active], key = attrgetter('name'))

    if len(sys.argv) > 1:
        series_list = []
        for i in sys.argv[1:]:
            try:
                series_list.append(ubuntu.getSeries(name_or_version = i))
            except HTTPError:
                print 'Error: %s is not a valid name or version' % i
        series_list.sort(key = attrgetter('name'))

    else:
        series_list = (ubuntu.current_series,)

    for series in series_list:
        print "Generating FTBFS for %s" % series.fullseriesname

        # Clear all caches and package lists
        PersonTeam.clear()
        SourcePackage.clear()
        SPPH.clear()
        components['main'] = []
        components['restricted'] = []
        components['universe'] = []
        components['multiverse'] = []

        for state in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload'):
            fetch_pkg_list(series, state)

        print "Generating HTML page..."
        generate_page(series)
        print "Generating CSV file..."
        generate_csvfile(series)
