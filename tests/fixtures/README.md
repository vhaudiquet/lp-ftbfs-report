# Test Fixture Format

This directory contains JSON test fixtures for testing lp-ftbfs-report with the DummyFetcher.

## File Format

See `schema.json` for the JSON schema definition.

### Structure

```json
{
  "archive": {
    "name": "archive-name",
    "displayname": "Human Readable Name",
    "is_ppa": false
  },
  "series": {
    "name": "oracular",
    "fullseriesname": "Ubuntu Oracular",
    "self_link": "https://api.launchpad.net/..."
  },
  "builds": [
    {
      "source_package_name": "pkg-name",
      "source_package_version": "1.0-1",
      "arch_tag": "amd64",
      "buildstate": "Failed to build",
      "datebuilt": "2026-04-01T12:00:00+00:00",
      "current_source_publication_link": "https://...",
      "build_log_url": "https://...",
      "component_name": "universe",
      "pocket": "Release",
      "is_current": true
    }
  ],
  "publications": {
    "publication-link": {
      "source_package_name": "pkg-name",
      "component_name": "universe",
      "pocket": "Release"
    }
  },
  "packagesets": {
    "set-name": ["pkg1", "pkg2"]
  },
  "teams": {
    "team-name": ["pkg1", "pkg2"]
  },
  "bugs": {
    "pkg-name": [
      {"id": 123, "title": "Bug title", "tags": ["ftbfs"]}
    ]
  },
  "reference_builds": {
    "pkg-name": {
      "amd64": {
        "buildstate": "Successfully built",
        "datebuilt": "2026-03-01T10:00:00+00:00"
      }
    }
  }
}
```

## Available Fixtures

- **sample.json**: Basic example with 3 packages showing different failure types
  - `example-pkg`: FTBFS on amd64 and arm64
  - `depwait-pkg`: Dependency wait
  - `always-fail-pkg`: Never built successfully before

## Build States, Components, Pockets

Valid build states:
- `"Successfully built"`
- `"Failed to build"`
- `"Dependency wait"`
- `"Chroot problem"`
- `"Failed to upload"`
- `"Cancelled build"`

Valid components: `main`, `restricted`, `universe`, `multiverse`

Valid pockets: `Release`, `Updates`, `Proposed`, `Security`

## Usage in Tests

```python
from lp_ftbfs_report.fetchers import DummyFetcher

fetcher = DummyFetcher("tests/fixtures/sample.json")
builds = list(fetcher.get_build_records("Failed to build", ["amd64"]))
```
