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
import re
import genshi.template

cachedir = '/home/michael/.launchpadlib/cache/'

credentials = Credentials()
credentials.load(open('/home/michael/.launchpadlib/ftbfs-credentials'))
launchpad = Launchpad(credentials, EDGE_SERVICE_ROOT, cachedir)

default_archlist = ('i386', 'amd64', 'sparc', 'powerpc', 'armel', 'ia64', 'lpia', 'hppa')

ubuntu = launchpad.distributions['ubuntu']
dev_series = ubuntu.current_series

all_packages = {}

build_state = {
		'Failed to build': 'FAILEDTOBUILD',
		'Dependency wait': 'MANUALDEPWAIT',
		'Chroot problem': 'CHROOTWAIT',
		'Failed to upload': 'UPLOADFAIL',
		'Needs building': 'PENDING',
		}

class PkgStat(object):
	def __init__(self, pkg):
		self.name = pkg.source_package_name
		self.version = pkg.source_package_version
		self.component = pkg.component_name
		self.url = 'https://launchpad.net/ubuntu/%s/+source/%s' % (dev_series.name, self.version)

	def __getattr__(self, attr):
		return None

	def empty(self, archlist = default_archlist):
		for arch in archlist:
			x = getattr(self, arch)
			if x and x[0] != 'PENDING':
				return False
		return True

def fetch_pkg_list(status):
	print "Processing '%s'" % status

	pkg_list = dev_series.getBuildRecords(build_state = status)

	for pkg in pkg_list:
		if not pkg.current_source_publication:
			# Build log for an older version
			continue
		print "  %s" % pkg.title
		srcpkg = pkg.current_source_publication.source_package_name
		version = pkg.current_source_publication.source_package_version
		if pkg.current_source_publication.status != 'Published':
			print "E: ", pkg.current_source_publication.status
			continue
		if not srcpkg in all_packages:
			all_packages[srcpkg] = PkgStat(pkg.current_source_publication)
		entry = all_packages[srcpkg]
		if version == entry.version:
			state = build_state[pkg.buildstate]
			if state == 'UPLOADFAIL':
				setattr(entry, pkg.arch_tag, (state, pkg.upload_log_url))
			else:
				setattr(entry, pkg.arch_tag, (state, pkg.build_log_url))

def generate_page(filename, template, archlist = default_archlist):
	try:
                out = open(filename, 'w')
        except IOError:
                return

	# split components
        data = {}
        for comp in ('main', 'restricted', 'universe', 'multiverse'):
                data[comp] = [item[1] for item in sorted(all_packages.items()) if item[1].component == comp and not item[1].empty(archlist)]

	# compute some statistics (number of packages for each build failure type)
        stats = {}
        for status in ('FAILEDTOBUILD', 'MANUALDEPWAIT', 'CHROOTWAIT', 'UPLOADFAIL', 'PENDING'):
                stats[status] = {}
                for arch in archlist:
                        stats[status][arch] = len([pkg for pkg in all_packages.values() if getattr(pkg, arch) and getattr(pkg, arch)[0] == status])
                        if stats[status][arch] == 0:
                                stats[status][arch] = None
        data['stats'] = stats
        data['release'] = dev_series.name

        loader = genshi.template.TemplateLoader(['.'])
        tmpl = loader.load(template)
        stream = tmpl.generate(**data)
        out.write(stream.render(method = 'xhtml'))
        out.close()

if __name__ == '__main__':
        for status in ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload'):
                fetch_pkg_list(status)
        
        generate_page('index.html', 'build_status.html')
