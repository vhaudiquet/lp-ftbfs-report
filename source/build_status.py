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

default_arch_list = ('i386', 'amd64', 'sparc', 'powerpc', 'armel', 'ia64')
apt_pkg.InitSystem()

# copied from ubuntu-dev-tools, libsupport.py:
def translate_api_web(self_url):
    return self_url.replace("api.", "").replace("beta/", "").replace("edge.", "")

# copied from ubuntu-dev-tools, lpapiapicache.py:
# TODO: use lpapicache from u-d-t
class PersonTeam(object):
        '''
        Wrapper class around a LP person or team object.
        '''

        _cache = dict() # Key is the LP API person/team URL

        def __init__(self, personteam):
                if isinstance(personteam, Entry) and personteam.resource_type_link in \
                                ('https://api.launchpad.net/beta/#person', 'https://api.launchpad.net/beta/#team'):
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
		all_packages[self.name] = self

	def isFTBFS(self, arch_list = default_arch_list):
		''' Returns True if at least one FTBFS exists. '''
		for ver in self.versions:
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
				self.log = translate_api_web(build.build_log_url)

			if self.buildstate == 'MANUALDEPWAIT':
				self.tooltip = 'waits on %s' % build.dependencies
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


def fetch_pkg_list(series, state):
	print "Processing '%s'" % state

	buildlist = series.getBuildRecords(build_state = state)

	for build in buildlist:
		csp_link = build.current_source_publication_link
		if not csp_link:
			# Build log for an older version
			continue

		print "  %s" % build.title

		spph = all_spph.get(csp_link)
		if not spph:
			spph = all_spph[csp_link] = SPPH(csp_link)
		spph.addBuildLog(build)

def generate_page(series, template = 'build_status.html', arch_list = default_arch_list):
	try:
		out = open('../%s.html' % series.name, 'w')
	except IOError:
		return

	# split components
	data = {}
	for comp in ('main', 'restricted', 'universe', 'multiverse'):
		data[comp] = [item for item in sorted(all_packages.values(), key = lambda x: x.name) \
				if item.component == comp and item.isFTBFS(arch_list)]

	# container object to hold the counts and the tooltip
	class StatData(object):
		def __init__(self, cnt, tooltip):
			self.cnt = cnt
			self.tooltip = tooltip

	# compute some statistics (number of packages for each build failure type)
	stats = {}
	for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'PENDING'):
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

	loader = genshi.template.TemplateLoader(['.'])
	tmpl = loader.load(template)
	stream = tmpl.generate(**data)
	out.write(stream.render(method = 'xhtml'))
	out.close()

def generate_csvfile(series, arch_list = default_arch_list):
	csvout = open('../%s.csv' % series.name, 'w')
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
		launchpad = Launchpad.login_anonymously('qa-ftbfs', 'production')
	else:
		LPNET_SERVICE_ROOT = 'https://api.launchpad.net/beta/'
		launchpad = Launchpad.login('qa-ftbfs', '', '', LPNET_SERVICE_ROOT)

	return launchpad

if __name__ == '__main__':
	launchpad = lp_login()

	ubuntu = launchpad.distributions['ubuntu']
	active_series_list = sorted([s for s in ubuntu.series if s.active], key = lambda x: x.name)

	if len(sys.argv) > 1:
		series_list = []
		for i in sys.argv[1:]:
			try:
				series_list.append(ubuntu.getSeries(name_or_version = i))
			except HTTPError:
				print 'Error: %s is not a valid name or version' % i
		series_list.sort(key = lambda x: x.name)

	else:
		series_list = (ubuntu.current_series,)

	for series in series_list:
		print "Generating FTBFS for %s" % series.fullseriesname

		# Reset package list
		all_packages = dict()
		all_spph = dict()

		# 'Needs building' makes it really run long, so not included in the status to fetch
		for state in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload'):
			fetch_pkg_list(series, state)

		print "Generating HTML page..."
		generate_page(series)
		print "Generating CSV file..."
		generate_csvfile(series)
