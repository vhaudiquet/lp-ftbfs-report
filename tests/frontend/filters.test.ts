/**
 * Unit tests for frontend filtering functionality
 * Tests the JavaScript filter logic using Happy DOM and the test-archive-oracular fixture
 */

// @ts-nocheck - Happy DOM types don't perfectly match browser DOM, but tests work at runtime

/// <reference types="bun-types" />

import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Document as HappyDocument } from 'happy-dom';
import { Window } from 'happy-dom';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Type alias for the document we use in tests
type TestDocument = HappyDocument;

interface TestContext {
  window: Window;
  document: TestDocument;
}

/**
 * Load and parse the test HTML file
 */
function loadTestHTML(): TestContext {
  const htmlPath = path.join(__dirname, '..', '..', 'src', 'lp_ftbfs_report', 'test-archive-oracular.html');
  const htmlContent = fs.readFileSync(htmlPath, 'utf-8');

  const window = new Window();
  const document = window.document;

  // Ensure SyntaxError is available (Bun + Happy DOM compatibility)
  if (!window.SyntaxError) {
    (window as any).SyntaxError = SyntaxError;
  }
  if (!window.Error) {
    (window as any).Error = Error;
  }
  if (!window.TypeError) {
    (window as any).TypeError = TypeError;
  }

  // Set up global scope for the module
  (global as any).document = document;
  (global as any).window = window;

  document.write(htmlContent);

  return { window, document };
}

/**
 * Wait for DOM to be ready and initialize filters
 */
async function waitForDOM(window: Window, document: TestDocument): Promise<void> {
  return new Promise((resolve) => {
    // Load and execute the filters script
    const filtersPath = path.join(__dirname, '..', '..', 'src', 'lp_ftbfs_report', 'filters.js');
    const filtersCode = fs.readFileSync(filtersPath, 'utf-8');

    // Execute the filters code using Function constructor (works in Happy DOM)
    const executeInContext = new Function('window', 'document', filtersCode);
    executeInContext(window, document);

    // Give filters time to initialize
    setTimeout(() => resolve(), 50);
  });
}

/**
 * Get visible rows in a table
 */
function getVisibleRows(document: TestDocument, tableSelector: string = '.filterable-table'): any[] {
  const tables = document.querySelectorAll(tableSelector);
  const allVisibleRows: any[] = [];

  tables.forEach((table) => {
    const rows = table.querySelectorAll('tbody tr');
    rows.forEach((row) => {
      const htmlRow = row as any;
      if (htmlRow.style && htmlRow.style.display !== 'none') {
        allVisibleRows.push(row);
      }
    });
  });

  return allVisibleRows;
}

/**
 * Get all rows in a table
 */
function getAllRows(document: TestDocument, tableSelector: string = '.filterable-table'): any[] {
  const tables = document.querySelectorAll(tableSelector);
  const allRows: any[] = [];

  tables.forEach((table) => {
    const rows = table.querySelectorAll('tbody tr');
    rows.forEach((row) => {
      allRows.push(row);
    });
  });

  return allRows;
}

/**
 * Set architecture mode (show/hide/only)
 */
function setArchMode(document: TestDocument, arch: string, mode: 'show' | 'hide' | 'only'): any {
  const radio = document.querySelector(`input[type="radio"][data-arch="${arch}"][data-mode="${mode}"]`);
  if (radio) {
    (radio as any).checked = true;
  }
  return radio;
}

/**
 * Trigger filter update
 */
function triggerFilterUpdate(document: TestDocument, window: Window, specificInput: any = null): void {
  const event = new window.Event('change', { bubbles: true });
  const input =
    specificInput ||
    document.querySelector('input[type="radio"][data-arch]:checked') ||
    document.querySelector('.filter-checkbox');
  if (input) {
    input.dispatchEvent(event);
  }
}

describe('Frontend Filters', () => {
  let window: Window;
  let document: TestDocument;

  beforeEach(async () => {
    const result = loadTestHTML();
    window = result.window;
    document = result.document;
    await waitForDOM(window, document);
  });

  afterEach(() => {
    if (window) {
      window.close();
    }
    // Clean up global variables
    delete (global as any).document;
    delete (global as any).window;
  });

  describe('Initial State', () => {
    test('all arch radios should default to "show" mode', () => {
      const archRadios = document.querySelectorAll('input[type="radio"][data-arch]');
      const statusFilters = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');

      expect(archRadios.length).toBeGreaterThan(0);
      expect(statusFilters.length).toBeGreaterThan(0);

      // Get unique architectures
      const archs = new Set<string>();
      archRadios.forEach((radio: any) => {
        const arch = radio.getAttribute('data-arch');
        if (arch) archs.add(arch);
      });

      // Each arch should have "show" mode selected by default
      archs.forEach((arch) => {
        const showRadio: any = document.querySelector(`input[type="radio"][data-arch="${arch}"][data-mode="show"]`);
        expect(showRadio).not.toBeNull();
        expect(showRadio?.checked).toBe(true);
      });

      // All status filters should be checked
      statusFilters.forEach((checkbox: any) => {
        expect(checkbox.checked).toBe(true);
      });
    });

    test('all rows should be visible initially', () => {
      const allRows = getAllRows(document);
      const visibleRows = getVisibleRows(document);

      expect(allRows.length).toBeGreaterThan(0);
      expect(visibleRows.length).toBe(allRows.length);
    });

    test('package search input should be empty', () => {
      const searchInput: any = document.getElementById('package-search');
      expect(searchInput).not.toBeNull();
      expect(searchInput?.value).toBe('');
      expect(searchInput.value).toBe('');
    });
  });

  describe('Architecture Filters', () => {
    test('setting an architecture to "hide" should hide rows with only that architecture', () => {
      const archRadios = document.querySelectorAll('input[type="radio"][data-arch]');
      expect(archRadios.length).toBeGreaterThan(0);

      const initialVisibleCount = getVisibleRows(document).length;

      // Get first architecture and set it to "hide"
      const firstArch = archRadios[0].getAttribute('data-arch');
      setArchMode(document, firstArch, 'hide');
      triggerFilterUpdate(document, window);

      const afterFilterCount = getVisibleRows(document).length;

      // Should have fewer or equal visible rows
      expect(afterFilterCount).toBeLessThanOrEqual(initialVisibleCount);
    });

    test('setting all architectures to "hide" should hide all rows', () => {
      const archRadios = document.querySelectorAll('input[type="radio"][data-arch]');

      // Get unique architectures
      const archs = new Set();
      archRadios.forEach((radio) => {
        archs.add(radio.getAttribute('data-arch'));
      });

      // Set all to hide
      archs.forEach((arch) => {
        setArchMode(document, arch, 'hide');
      });
      triggerFilterUpdate(document, window);

      const visibleRows = getVisibleRows(document);
      expect(visibleRows.length).toBe(0);
    });

    test('setting only one architecture to "show" with others to "hide" should show only packages with that arch', () => {
      const archRadios = document.querySelectorAll('input[type="radio"][data-arch]');

      // Get unique architectures
      const archs = [...new Set(Array.from(archRadios).map((r) => r.getAttribute('data-arch')))];

      // Set all to hide except first
      archs.forEach((arch, index) => {
        if (index === 0) {
          setArchMode(document, arch, 'show');
        } else {
          setArchMode(document, arch, 'hide');
        }
      });
      triggerFilterUpdate(document, window);

      const visibleRows = getVisibleRows(document);

      // Should still have some visible rows (packages failing on the "show" arch)
      expect(visibleRows.length).toBeGreaterThan(0);

      // Verify visible rows have failures on the selected arch
      const selectedArch = archs[0];
      visibleRows.forEach((row) => {
        const cells = row.querySelectorAll(`[data-arch="${selectedArch}"]`);
        // Row should have at least one failure on the selected arch
        expect(cells.length).toBeGreaterThan(0);
      });
    });
  });

  describe('Status Filters', () => {
    test('unchecking a status should hide packages with only that status', () => {
      const statusCheckboxes = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');
      expect(statusCheckboxes.length).toBeGreaterThan(0);

      const initialVisibleCount = getVisibleRows(document).length;

      // Uncheck the first status
      statusCheckboxes[0].checked = false;
      triggerFilterUpdate(document, window);

      const afterFilterCount = getVisibleRows(document).length;

      // Should have fewer visible rows (or same if packages have other statuses)
      expect(afterFilterCount).toBeLessThanOrEqual(initialVisibleCount);
    });

    test('unchecking all statuses should hide all rows', () => {
      const statusCheckboxes = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');

      statusCheckboxes.forEach((checkbox) => {
        checkbox.checked = false;
      });
      triggerFilterUpdate(document, window);

      const visibleRows = getVisibleRows(document);
      expect(visibleRows.length).toBe(0);
    });

    test('checking only FAILEDTOBUILD should show only packages with that status', () => {
      const statusCheckboxes = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');

      // Uncheck all
      statusCheckboxes.forEach((checkbox) => {
        checkbox.checked = false;
      });

      // Check only FAILEDTOBUILD
      const ftbfsCheckbox = Array.from(statusCheckboxes).find((cb) => cb.value === 'FAILEDTOBUILD');

      if (ftbfsCheckbox) {
        ftbfsCheckbox.checked = true;
        triggerFilterUpdate(document, window);

        const visibleRows = getVisibleRows(document);

        // All visible rows should have at least one FAILEDTOBUILD cell
        visibleRows.forEach((row) => {
          const ftbfsCells = row.querySelectorAll('[data-status="FAILEDTOBUILD"]');
          expect(ftbfsCells.length).toBeGreaterThan(0);
        });
      }
    });
  });

  describe('Combined Arch and Status Filters', () => {
    test('filtering by both arch and status should show only matching packages', () => {
      const archCheckboxes = document.querySelectorAll('.filter-checkbox[data-filter-type="arch"]');
      const statusCheckboxes = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');

      // Uncheck all
      archCheckboxes.forEach((checkbox) => {
        checkbox.checked = false;
      });
      statusCheckboxes.forEach((checkbox) => {
        checkbox.checked = false;
      });

      // Check only first arch and FAILEDTOBUILD status
      if (archCheckboxes.length > 0) {
        const firstArch = archCheckboxes[0];
        firstArch.checked = true;
        const archValue = firstArch.value;

        const ftbfsCheckbox = Array.from(statusCheckboxes).find((cb) => cb.value === 'FAILEDTOBUILD');

        if (ftbfsCheckbox) {
          ftbfsCheckbox.checked = true;
          triggerFilterUpdate(document, window);

          const visibleRows = getVisibleRows(document);

          // All visible rows should have a FAILEDTOBUILD cell in the selected arch
          visibleRows.forEach((row) => {
            const matchingCells = row.querySelectorAll(`[data-arch="${archValue}"][data-status="FAILEDTOBUILD"]`);
            expect(matchingCells.length).toBeGreaterThan(0);
          });
        }
      }
    });
  });

  describe('Package Search Filter', () => {
    test('searching for a package name should filter rows', () => {
      const searchInput = document.getElementById('package-search');
      const allRows = getAllRows(document);

      if (allRows.length > 0) {
        // Get the first package name
        const firstRow = allRows[0];
        const packageName = firstRow.getAttribute('data-package-name');

        if (packageName) {
          // Search for first 3 characters
          const searchTerm = packageName.substring(0, 3);
          searchInput.value = searchTerm;

          const inputEvent = new window.Event('input', { bubbles: true });
          searchInput.dispatchEvent(inputEvent);

          const visibleRows = getVisibleRows(document);

          // All visible rows should contain the search term
          visibleRows.forEach((row) => {
            const rowPackageName = row.getAttribute('data-package-name');
            expect(rowPackageName.toLowerCase()).toContain(searchTerm.toLowerCase());
          });
        }
      }
    });

    test('search is case-insensitive', () => {
      const searchInput = document.getElementById('package-search');
      const allRows = getAllRows(document);

      if (allRows.length > 0) {
        const firstRow = allRows[0];
        const packageName = firstRow.getAttribute('data-package-name');

        if (packageName) {
          // Search with uppercase
          const searchTerm = packageName.substring(0, 3).toUpperCase();
          searchInput.value = searchTerm;

          const inputEvent = new window.Event('input', { bubbles: true });
          searchInput.dispatchEvent(inputEvent);

          const visibleRows = getVisibleRows(document);
          expect(visibleRows.length).toBeGreaterThan(0);

          // All visible rows should match
          visibleRows.forEach((row) => {
            const rowPackageName = row.getAttribute('data-package-name');
            expect(rowPackageName.toLowerCase()).toContain(searchTerm.toLowerCase());
          });
        }
      }
    });

    test('search with no matches should hide all rows', () => {
      const searchInput = document.getElementById('package-search');

      searchInput.value = 'xyznonexistentpackagexyz';
      const inputEvent = new window.Event('input', { bubbles: true });
      searchInput.dispatchEvent(inputEvent);

      const visibleRows = getVisibleRows(document);
      expect(visibleRows.length).toBe(0);
    });
  });

  describe('Reset Filters', () => {
    test('reset button should restore all filters to default', () => {
      const resetButton = document.getElementById('reset-filters');
      const allCheckboxes = document.querySelectorAll('.filter-checkbox');
      const searchInput = document.getElementById('package-search');

      // Modify filters
      allCheckboxes.forEach((checkbox) => {
        checkbox.checked = false;
      });
      searchInput.value = 'test';
      triggerFilterUpdate(document, window);

      // Verify filters are applied
      let visibleRows = getVisibleRows(document);
      expect(visibleRows.length).toBe(0);

      // Reset
      resetButton.click();

      // Arch and status checkboxes should be checked, arch-only should be unchecked
      const archFilters = document.querySelectorAll('.filter-checkbox[data-filter-type="arch"]');
      const statusFilters = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');
      const archOnlyFilters = document.querySelectorAll('.filter-checkbox[data-filter-type="arch-only"]');

      archFilters.forEach((checkbox) => {
        expect(checkbox.checked).toBe(true);
      });
      statusFilters.forEach((checkbox) => {
        expect(checkbox.checked).toBe(true);
      });
      archOnlyFilters.forEach((checkbox) => {
        expect(checkbox.checked).toBe(false);
      });

      // Search should be empty
      expect(searchInput.value).toBe('');

      // All rows should be visible
      visibleRows = getVisibleRows(document);
      const allRows = getAllRows(document);
      expect(visibleRows.length).toBe(allRows.length);
    });
  });

  describe('Table Container Visibility', () => {
    test('table containers should be hidden when no rows are visible', () => {
      const allCheckboxes = document.querySelectorAll('.filter-checkbox');

      // Uncheck all filters
      allCheckboxes.forEach((checkbox) => {
        checkbox.checked = false;
      });
      triggerFilterUpdate(document, window);

      // Find table containers with filterable tables
      const tables = document.querySelectorAll('.filterable-table');
      tables.forEach((table) => {
        const container = table.closest('.table-container');
        if (container) {
          const visibleRows = Array.from(table.querySelectorAll('tbody tr')).filter(
            (row) => row.style.display !== 'none'
          );

          if (visibleRows.length === 0) {
            expect(container.style.display).toBe('none');
          }
        }
      });
    });

    test('table containers should be visible when rows are visible', () => {
      // All filters checked by default
      const tables = document.querySelectorAll('.filterable-table');

      tables.forEach((table) => {
        const container = table.closest('.table-container');
        const rows = table.querySelectorAll('tbody tr');

        if (rows.length > 0 && container) {
          expect(container.style.display).not.toBe('none');
        }
      });
    });
  });

  describe('Data Attributes', () => {
    test('all table rows should have data-package-name attribute', () => {
      const allRows = getAllRows(document);

      allRows.forEach((row) => {
        expect(row.hasAttribute('data-package-name')).toBe(true);
        expect(row.getAttribute('data-package-name')).toBeTruthy();
      });
    });

    test('build cells should have data-arch and data-status attributes', () => {
      const allRows = getAllRows(document);

      allRows.forEach((row) => {
        const buildCells = row.querySelectorAll('.build-cell');

        buildCells.forEach((cell) => {
          if (!cell.classList.contains('empty-cell')) {
            expect(cell.hasAttribute('data-arch')).toBe(true);
            expect(cell.hasAttribute('data-status')).toBe(true);
            expect(cell.getAttribute('data-arch')).toBeTruthy();
            expect(cell.getAttribute('data-status')).toBeTruthy();
          }
        });
      });
    });
  });

  describe('Filter Controls', () => {
    test('all arch radio buttons should have correct data attributes', () => {
      const archRadios = document.querySelectorAll('input[type="radio"][data-arch]');

      expect(archRadios.length).toBeGreaterThan(0);
      archRadios.forEach((radio) => {
        expect(radio.hasAttribute('data-arch')).toBe(true);
        expect(radio.hasAttribute('data-mode')).toBe(true);
        expect(radio.getAttribute('data-arch')).toBeTruthy();
        expect(['show', 'hide', 'only']).toContain(radio.getAttribute('data-mode'));
      });
    });

    test('all status filter checkboxes should have correct data-filter-type', () => {
      const statusCheckboxes = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');

      expect(statusCheckboxes.length).toBeGreaterThan(0);
      statusCheckboxes.forEach((checkbox) => {
        expect(checkbox.getAttribute('data-filter-type')).toBe('status');
        expect(checkbox.value).toBeTruthy();
      });
    });
  });

  describe('"Only" Mode Filters', () => {
    test('"only" mode should show packages that fail only on selected arch', async () => {
      // Create test HTML with packages that fail on different architectures
      const testHTML = `
        <input type="text" id="package-search" placeholder="Search">
        <button id="reset-filters">Reset</button>
        
        <div id="arch-filters">
          <input type="radio" name="arch-amd64" data-arch="amd64" data-mode="show" id="amd64-show" checked>
          <input type="radio" name="arch-amd64" data-arch="amd64" data-mode="hide" id="amd64-hide">
          <input type="radio" name="arch-amd64" data-arch="amd64" data-mode="only" id="amd64-only">
          
          <input type="radio" name="arch-arm64" data-arch="arm64" data-mode="show" id="arm64-show" checked>
          <input type="radio" name="arch-arm64" data-arch="arm64" data-mode="hide" id="arm64-hide">
          <input type="radio" name="arch-arm64" data-arch="arm64" data-mode="only" id="arm64-only">
        </div>
        
        <div id="status-filters">
          <input type="checkbox" class="filter-checkbox" data-filter-type="status" value="FAILEDTOBUILD" checked>
        </div>
        
        <div class="table-container">
          <table class="filterable-table">
            <thead><tr><th>Package</th><th>amd64</th><th>arm64</th></tr></thead>
            <tbody>
              <tr data-package-name="fails-amd64-only">
                <td>fails-amd64-only</td>
                <td data-arch="amd64" data-status="FAILEDTOBUILD">FTBFS</td>
                <td class="empty-cell">—</td>
              </tr>
              <tr data-package-name="fails-arm64-only">
                <td>fails-arm64-only</td>
                <td class="empty-cell">—</td>
                <td data-arch="arm64" data-status="FAILEDTOBUILD">FTBFS</td>
              </tr>
              <tr data-package-name="fails-both">
                <td>fails-both</td>
                <td data-arch="amd64" data-status="FAILEDTOBUILD">FTBFS</td>
                <td data-arch="arm64" data-status="FAILEDTOBUILD">FTBFS</td>
              </tr>
            </tbody>
          </table>
        </div>
      `;

      document.body.innerHTML = testHTML;
      await waitForDOM(window, document);

      // Set amd64 to "only" mode
      setArchMode(document, 'amd64', 'only');
      triggerFilterUpdate(document, window);

      const visibleRows = getVisibleRows(document);
      const visiblePackages = visibleRows.map((row) => row.getAttribute('data-package-name'));

      // Should only show packages that fail ONLY on amd64 (not arm64)
      expect(visiblePackages).toContain('fails-amd64-only');
      expect(visiblePackages).not.toContain('fails-arm64-only');
      expect(visiblePackages).not.toContain('fails-both');
    });

    test('"only" mode filtering works correctly and can be switched back to show mode', async () => {
      const testHTML = `
        <input type="text" id="package-search" placeholder="Search">
        <button id="reset-filters">Reset</button>
        
        <div id="arch-filters">
          <input type="radio" name="arch-amd64" data-arch="amd64" data-mode="show" id="amd64-show" checked>
          <input type="radio" name="arch-amd64" data-arch="amd64" data-mode="hide" id="amd64-hide">
          <input type="radio" name="arch-amd64" data-arch="amd64" data-mode="only" id="amd64-only">
          
          <input type="radio" name="arch-arm64" data-arch="arm64" data-mode="show" id="arm64-show" checked>
          <input type="radio" name="arch-arm64" data-arch="arm64" data-mode="hide" id="arm64-hide">
          <input type="radio" name="arch-arm64" data-arch="arm64" data-mode="only" id="arm64-only">
        </div>
        
        <div id="status-filters">
          <input type="checkbox" class="filter-checkbox" data-filter-type="status" value="FAILEDTOBUILD" checked>
        </div>
        
        <div class="table-container">
          <table class="filterable-table">
            <thead><tr><th>Package</th><th>amd64</th><th>arm64</th></tr></thead>
            <tbody>
              <tr data-package-name="fails-amd64-only">
                <td>fails-amd64-only</td>
                <td data-arch="amd64" data-status="FAILEDTOBUILD">FTBFS</td>
                <td class="empty-cell">—</td>
              </tr>
              <tr data-package-name="fails-arm64-only">
                <td>fails-arm64-only</td>
                <td class="empty-cell">—</td>
                <td data-arch="arm64" data-status="FAILEDTOBUILD">FTBFS</td>
              </tr>
              <tr data-package-name="fails-both">
                <td>fails-both</td>
                <td data-arch="amd64" data-status="FAILEDTOBUILD">FTBFS</td>
                <td data-arch="arm64" data-status="FAILEDTOBUILD">FTBFS</td>
              </tr>
            </tbody>
          </table>
        </div>
      `;

      document.body.innerHTML = testHTML;
      await waitForDOM(window, document);

      // Set amd64 to "only" mode
      const amd64OnlyRadio = setArchMode(document, 'amd64', 'only');
      triggerFilterUpdate(document, window, amd64OnlyRadio);

      // Should only show packages that fail only on amd64
      let visibleRows = getVisibleRows(document);
      const visiblePackages = visibleRows.map((row) => row.getAttribute('data-package-name'));
      expect(visiblePackages).toContain('fails-amd64-only');
      expect(visiblePackages).not.toContain('fails-arm64-only');
      expect(visiblePackages).not.toContain('fails-both');

      // Switch amd64 back to "show" - all packages should be visible again
      const amd64ShowRadio = setArchMode(document, 'amd64', 'show');
      triggerFilterUpdate(document, window, amd64ShowRadio);

      visibleRows = getVisibleRows(document);
      expect(visibleRows.length).toBe(3); // All three packages should be visible
    });
  });
});
