/**
 * Build Status Report - Filtering Functionality
 * Provides interactive filtering for the build status tables
 */

(function() {
  'use strict';
  
  /**
   * Initialize the filtering functionality
   * Should be called when DOM is ready
   */
  function initializeFilters() {
    const filterCheckboxes = document.querySelectorAll('.filter-checkbox');
    const packageSearch = document.getElementById('package-search');
    const resetButton = document.getElementById('reset-filters');
    
    if (!packageSearch || !resetButton) {
      console.warn('Filter elements not found in DOM');
      return;
    }
    
    /**
     * Apply all active filters to the tables
     */
    function applyFilters() {
      const selectedArchs = Array.from(
        document.querySelectorAll('.filter-checkbox[data-filter-type="arch"]:checked')
      ).map(el => el.value);
      
      const selectedStatuses = Array.from(
        document.querySelectorAll('.filter-checkbox[data-filter-type="status"]:checked')
      ).map(el => el.value);
      
      const searchQuery = packageSearch.value.toLowerCase();
      
      const tables = document.querySelectorAll('.filterable-table');
      tables.forEach(table => {
        const rows = table.querySelectorAll('tbody tr');
        let visibleCount = 0;
        
        rows.forEach(row => {
          const packageName = row.getAttribute('data-package-name') || '';
          let isVisible = true;
          
          // Check package search filter first
          if (searchQuery && !packageName.toLowerCase().includes(searchQuery)) {
            isVisible = false;
          }
          
          // Check architecture filter (show if ANY cell matches selected archs)
          // If no archs are selected, hide the row (empty filter = show nothing)
          if (isVisible && selectedArchs.length === 0) {
            const buildCells = row.querySelectorAll('[data-arch]');
            // If there are build cells but no arch is selected, hide
            if (buildCells.length > 0) {
              isVisible = false;
            }
          } else if (isVisible && selectedArchs.length > 0) {
            const buildCells = row.querySelectorAll('[data-arch]');
            let archMatch = false;
            
            buildCells.forEach(cell => {
              const arch = cell.getAttribute('data-arch');
              if (selectedArchs.includes(arch)) {
                archMatch = true;
              }
            });
            
            isVisible = archMatch;
          }
          
          // Further filter by status if status filters are active
          // If no statuses are selected, hide the row (empty filter = show nothing)
          if (isVisible && selectedStatuses.length === 0) {
            const buildCells = row.querySelectorAll('[data-status]');
            // If there are build cells but no status is selected, hide
            if (buildCells.length > 0) {
              isVisible = false;
            }
          } else if (isVisible && selectedStatuses.length > 0) {
            const buildCells = row.querySelectorAll('[data-arch]');
            let statusMatch = false;
            
            buildCells.forEach(cell => {
              const status = cell.getAttribute('data-status');
              const arch = cell.getAttribute('data-arch');
              
              // Show if this cell matches selected status AND (no arch filter or arch matches)
              if (selectedStatuses.includes(status)) {
                if (selectedArchs.length === 0 || selectedArchs.includes(arch)) {
                  statusMatch = true;
                }
              }
            });
            
            isVisible = statusMatch;
          }
          
          row.style.display = isVisible ? '' : 'none';
          if (isVisible) visibleCount++;
        });
        
        // Update table visibility
        const container = table.closest('.table-container');
        if (container) {
          container.style.display = visibleCount > 0 ? '' : 'none';
        }
      });
    }
    
    // Add event listeners
    filterCheckboxes.forEach(checkbox => {
      checkbox.addEventListener('change', applyFilters);
    });
    
    packageSearch.addEventListener('input', applyFilters);
    
    resetButton.addEventListener('click', function() {
      filterCheckboxes.forEach(checkbox => checkbox.checked = true);
      packageSearch.value = '';
      applyFilters();
    });
    
    // Initial filtering on page load
    applyFilters();
  }

  // Auto-initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeFilters);
  } else {
    // DOM already loaded
    initializeFilters();
  }
  
  // Export for module usage (tests)
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { initializeFilters };
  }
  // Export for ES6 module usage
  if (typeof window !== 'undefined') {
    window.initializeFilters = initializeFilters;
  }
})();

