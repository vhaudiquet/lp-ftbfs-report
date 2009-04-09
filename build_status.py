#!/usr/bin/python
# -*- coding: UTF-8 -*-

# (C) 2007-2009 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

# Rewrite of the old build_status script using LP API

# Requirements:
# - python-launchpadlib
# - python-genshi

from launchpadlib.launchpad import Launchpad, EDGE_SERVICE_ROOT
from launchpadlib.credentials import Credentials
from launchpadlib.errors import HTTPError
import sys
import genshi.template

cachedir = '/home/michael/.launchpadlib/cache/'

credentials = Credentials()
credentials.load(open('/home/michael/.launchpadlib/ftbfs-credentials'))
launchpad = Launchpad(credentials, EDGE_SERVICE_ROOT, cachedir)

default_arch_list = ('i386', 'amd64', 'sparc', 'powerpc', 'armel', 'ia64', 'lpia', 'hppa')

ubuntu = launchpad.distributions['ubuntu']

active_series_list = sorted([s for s in ubuntu.series if s.active], key = lambda x: x.name)

class SourcePackage(object):
	class VersionInfo(object):
		def __init__(self, spph):
			self.version = spph.source_package_version
			self.pocket = spph.pocket
			self.logs = {}

		def getArch(self, arch):
			try:
				return self.logs[arch]
			except KeyError:
				return None

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
							
			if self.buildstate == 'UPLOADFAIL':
				self.log = build.upload_log_url
			else:
				self.log = build.build_log_url

			if self.buildstate == 'MANUALDEPWAIT':
				self.dependencies = build.dependencies

	def __init__(self, srcpkg):
		self.name = srcpkg.source_package_name
		self.component = srcpkg.component_name
		self.url = 'https://launchpad.net/ubuntu/+source/%s' % self.name
		self.versions = {}

	def addBuildLog(self, buildlog):
		spph = buildlog.current_source_publication
		try:
			version = self.versions[spph.source_package_version]
		except KeyError:
			version = self.VersionInfo(spph)
			self.versions[version.version] = version

		version.logs[buildlog.arch_tag] = self.BuildLog(buildlog)

	def isFTBFS(self, arch_list = default_arch_list):
		''' Returns True if at least one FTBFS exists. '''
		for ver in self.versions.values():
			for arch in arch_list:
				log = ver.getArch(arch)
				if log and log.buildstate != 'PENDING':
					return True
		return False

	def getCount(self, arch, state):
		count = 0
		for ver in self.versions.values():
			if arch in ver.logs and ver.logs[arch].buildstate == state:
				count += 1
		return count

def fetch_pkg_list(series, state):
	print "Processing '%s'" % state

	buildlist = series.getBuildRecords(build_state = state)

	for build in buildlist:

		if not build.current_source_publication:
			# Build log for an older version
			continue

		print "  %s" % build.title
		srcpkg = build.current_source_publication.source_package_name
		try:
			entry = all_packages[srcpkg]
		except KeyError:
			entry = SourcePackage(build.current_source_publication)
			all_packages[srcpkg] = entry
		entry.addBuildLog(build)

def generate_page(series, template = 'build_status.html', arch_list = default_arch_list):
	try:
		out = open('%s.html' % series.name, 'w')
	except IOError:
		return

	# split components
	data = {}
	for comp in ('main', 'restricted', 'universe', 'multiverse'):
		data[comp] = [item for item in sorted(all_packages.values(), key = lambda x: x.name) \
				if item.component == comp and item.isFTBFS(arch_list)]

	# compute some statistics (number of packages for each build failure type)
	stats = {}
	for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'PENDING'):
		stats[state] = {}
		for arch in arch_list:
			stats[state][arch] = sum([pkg.getCount(arch, state) for pkg in all_packages.values()])
			if stats[state][arch] == 0:
				stats[state][arch] = None

	data['stats'] = stats
	data['series'] = series
	data['active_series_list'] = active_series_list
	data['arch_list'] = arch_list

	loader = genshi.template.TemplateLoader(['.'])
	tmpl = loader.load(template)
	stream = tmpl.generate(**data)
	out.write(stream.render(method = 'xhtml'))
	out.close()

if __name__ == '__main__':
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
		all_packages = {}

		for state in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload'):
			fetch_pkg_list(series, state)
		generate_page(series)
