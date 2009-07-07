#!/usr/bin/python
# -*- coding: UTF-8 -*-

# Copyright © 2007-2009 Michael Bienia <geser@ubuntu.com>
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

#import httplib2
#httplib2.debuglevel = 1

from launchpadlib.launchpad import Launchpad, EDGE_SERVICE_ROOT
from launchpadlib.resource import Entry
from launchpadlib.credentials import Credentials
from launchpadlib.errors import HTTPError
import sys, os
import apt_pkg
import genshi.template

default_arch_list = ('i386', 'amd64', 'sparc', 'powerpc', 'armel', 'ia64', 'lpia')
apt_pkg.InitSystem()

class SourcePackage(object):
	class VersionList(list):
		def __getitem__(self, item):
			if isinstance(item, Entry):
				# A source_package_publishing_history object (hopefully)
				ver_list = [ver for ver in self if ver.version == item.source_package_version]
				if ver_list:
					return ver_list[0]
				else:
					version = SourcePackage.VersionInfo(item)
					self.append(version)
					# keep the version list sorted
					self.sort(key = lambda x: x.version, cmp = apt_pkg.VersionCompare)
					return version
			else:
				return self[item]

	class VersionInfo(object):
		def __init__(self, spph):
			self.version = spph.source_package_version
			self.pocket = spph.pocket
			self.logs = dict()

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
		self.versions = self.VersionList()

	def addBuildLog(self, buildlog, spph):
		version = self.versions[spph]
		version.logs[buildlog.arch_tag] = self.BuildLog(buildlog)

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

def fetch_pkg_list(series, state):
	print "Processing '%s'" % state

	buildlist = series.getBuildRecords(build_state = state)

	for build in buildlist:
		csp = build.current_source_publication
		if not csp:
			# Build log for an older version
			continue

		print "  %s" % build.title
		srcpkg = csp.source_package_name
		try:
			entry = all_packages[srcpkg]
		except KeyError:
			entry = SourcePackage(csp)
			all_packages[srcpkg] = entry
		entry.addBuildLog(build, csp)

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

	# compute some statistics (number of packages for each build failure type)
	stats = {}
	for state in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'PENDING'):
		stats[state] = {}
		for arch in arch_list:
			stats[state][arch] = sum([pkg.getCount(arch, state) for pkg in all_packages.values() if pkg.isFTBFS()])
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

	creddir = os.path.expanduser("~/.cache/lp_credentials")
	if not os.path.isdir(creddir):
		os.makedirs(creddir)
		os.chmod(creddir, 0700)

	# load stored LP credentials
	try:
		credfile = open(os.path.join(creddir, 'qa-ftbfs.txt'), 'r')
		credentials = Credentials()
		credentials.load(credfile)
		credfile.close()
		launchpad = Launchpad(credentials, EDGE_SERVICE_ROOT, cachedir)
	except IOError:
		launchpad = Launchpad.get_token_and_login('qa-ftbfs', EDGE_SERVICE_ROOT, cachedir)
		credfile = open(os.path.join(creddir, 'qa-ftbfs.txt'), 'w')
		launchpad.credentials.save(credfile)
		credfile.close()

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
		all_packages = {}

		# 'Needs building' makes it really run long, so not included in the status to fetch
		for state in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload'):
			fetch_pkg_list(series, state)
		print "Generating HTML page..."
		generate_page(series)
		print "Generating CSV file..."
		generate_csvfile(series)
