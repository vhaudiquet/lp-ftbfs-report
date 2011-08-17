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
# - python-genshi

# Uncomment for tracing LP API calls
#import httplib2
#httplib2.debuglevel = 1

from launchpadlib.launchpad import Launchpad
try:
    from launchpadlib.resource import Entry
except ImportError:
    from lazr.restfulclient.resource import Entry
from launchpadlib.errors import HTTPError
import sys, os
import apt_pkg
import genshi.template
try:
    from launchpadlib.uris import *
except ImportError:
    lookup_service_root = lambda u: 'https://api.launchpad.net/' if u == 'production' else 'https://api.edge.launchpad.net/'

lp_service = 'production'
api_version = 'devel'
default_arch_list = []
find_tagged_bugs = 'ftbfs'
apt_pkg.InitSystem()

# copied from ubuntu-dev-tools, libsupport.py:
def translate_api_web(self_url):
    return self_url.replace("api.", "").replace("%s/" % api_version, "").replace("edge.", "")

# copied from ubuntu-dev-tools, lpapiapicache.py:
# TODO: use lpapicache from u-d-t
class PersonTeam(object):
    '''
    Wrapper class around a LP person or team object.
    '''

    resource_type = (lookup_service_root(lp_service) + api_version + '/#person',
            lookup_service_root(lp_service) + api_version + '/#team')
    _cache = dict() # Key is the LP API person/team URL

    def __init__(self, personteam):
        if isinstance(personteam, Entry) and personteam.resource_type_link in self.resource_type:
            self._personteam = personteam
            # Add ourself to the cache
            if personteam.self_link not in self._cache:
                self._cache[personteam.self_link] = self
        else:
            raise TypeError('A LP API person or team representation expected.')

    def __str__(self):
        return u'%s (%s)' % (self._personteam.display_name, self._personteam.name)

    def __getattr__(self, attr):
        return getattr(self._personteam, attr)

    @classmethod
    def getPersonTeam(cls, name):
        '''
        Return a PersonTeam object for the LP user 'name'.

        'name' can be a LP id or a LP API URL for that person or team.
        '''

        if name in cls._cache:
            # 'name' is a LP API URL
            return cls._cache[name]
        else:
            if not name.startswith('http'):
                # Check if we've cached the 'name' already
                for personteam in cls._cache.values():
                    if personteam.name == name:
                        return personteam

            try:
                return PersonTeam(launchpad.people[name])
            except HTTPError, e:
                if e.response.status == 410:
                    return None
                else:
                    raise

class SourcePackage(object):
    class VersionList(list):
        def append(self, item):
            super(SourcePackage.VersionList, self).append(item)
            self.sort(key = lambda x: x.version, cmp = apt_pkg.VersionCompare)

    def __init__(self, srcpkg):
        self.name = srcpkg.source_package_name
        self.component = srcpkg.component_name
        self.url = 'https://launchpad.net/ubuntu/+source/%s' % self.name
        self.versions = self.VersionList()

        if find_tagged_bugs is None:
            self.tagged_bugs = []
        else:
            ts = ubuntu.getSourcePackage(name=self.name).searchTasks(tags=find_tagged_bugs)
            self.tagged_bugs = [t.bug for t in ts]
        all_packages[self.name] = self

    def isFTBFS(self, arch_list = default_arch_list, current = True):
        ''' Returns True if at least one FTBFS exists. '''
        for ver in self.versions:
            if ver.current != current:
                continue
            for arch in arch_list:
                log = ver.getArch(arch)
                if log and log.buildstate != 'PENDING':
                    return True
        return False

    def getCount(self, arch, state):
        count = 0
        for ver in self.versions:
            if arch in ver.logs and ver.logs[arch].buildstate == state:
                count += 1
        return count

class SPPH(object):
    def __init__(self, spph_link):
        self.spph = launchpad.load(spph_link)
        self.logs = dict()
        self.version = self.spph.source_package_version
        self.pocket = self.spph.pocket
        self.changed_by = PersonTeam.getPersonTeam(self.spph.package_creator_link)
        #self.signed_by = spph.package_signer_link and PersonTeam.getPersonTeam(self.spph.package_signer_link)
        self.srcpkg = all_packages.get(self.spph.source_package_name)
        if not self.srcpkg:
            self.srcpkg = SourcePackage(self.spph)
        self.srcpkg.versions.append(self)
        self.current = None

    class BuildLog(object):
        def __init__(self, build):
            buildstates = {
                    'Failed to build': 'FAILEDTOBUILD',
                    'Dependency wait': 'MANUALDEPWAIT',
                    'Chroot problem': 'CHROOTWAIT',
                    'Failed to upload': 'UPLOADFAIL',
                    'Needs building': 'PENDING',
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


def fetch_pkg_list(archive, series, state, main_archive=None, main_series=None):
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

        print "  %s" % build.title

        spph = all_spph.get(csp_link)
        if not spph:
            spph = all_spph[csp_link] = SPPH(csp_link)

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
                    source_name=spph.spph.source_package_name,
                    version=spph.spph.source_package_version,
                    status='Published')
                spph.current = len(main_publications[:1]) > 0
            else:
                spph.current = True

        if not spph.current:
            print "    superseded"
        spph.addBuildLog(build)

def generate_page(archive, series, template = 'build_status.html', arch_list = default_arch_list):
    try:
        out = open('../%s-%s.html' % (archive.name, series.name), 'w')
    except IOError:
        return

    # split components
    data = {}
    for comp in ('main', 'restricted', 'universe', 'multiverse'):
        data[comp] = [item for item in sorted(all_packages.values(), key = lambda x: x.name) \
                if item.component == comp and item.isFTBFS(arch_list, True)]
        data['%s_superseded' % comp] = [item for item in sorted(all_packages.values(), key = lambda x: x.name) \
                if item.component == comp and item.isFTBFS(arch_list, False)]

    # container object to hold the counts and the tooltip
    class StatData(object):
        def __init__(self, cnt, cnt_superseded, tooltip):
            self.cnt = cnt
            self.cnt_superseded = cnt_superseded
            self.tooltip = tooltip

    # compute some statistics (number of packages for each build failure type)
    stats = {}
    for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'PENDING'):
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

    loader = genshi.template.TemplateLoader(['.'])
    tmpl = loader.load(template)
    stream = tmpl.generate(**data)
    out.write(stream.render(method = 'xhtml'))
    out.close()

def generate_csvfile(archive, series, arch_list = default_arch_list):
    csvout = open('../%s-%s.csv' % (archive.name, series.name), 'w')
    linetemplate = '%(name)s,%(link)s,%(explain)s\n'
    for pkg in all_packages.values():
        for ver in pkg.versions:
            for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'PENDING'):
                archs = [ arch for (arch, log) in ver.logs.items() if log.buildstate == state ]
                if archs:
                    log = ver.logs[archs[0]].log
                    csvout.write(linetemplate  % {'name': pkg.name, 'link': log,
                        'explain':"[%s] %s" %(','.join(archs), state)})

def lp_login():
    cachedir = os.path.expanduser('~/.cache/launchpadlib/')
    if not os.path.isdir(cachedir):
        os.makedirs(cachedir)

    # login anonymously to LP
    if hasattr(Launchpad, 'login_anonymously'):
        launchpad = Launchpad.login_anonymously('qa-ftbfs', lp_service, version=api_version)
    else:
        launchpad = Launchpad.login('qa-ftbfs', '', '', lookup_service_root(lp_service))

    return launchpad

if __name__ == '__main__':
    launchpad = lp_login()

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

        # Reset package list
        all_packages = dict()
        all_spph = dict()

        # 'Needs building' makes it really run long, so not included in the status to fetch
        for state in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload'):
            fetch_pkg_list(archive, series, state, main_archive, main_series)

        print "Generating HTML page..."
        generate_page(archive, series)
        print "Generating CSV file..."
        generate_csvfile(archive, series)
