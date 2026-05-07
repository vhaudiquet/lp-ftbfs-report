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
    const archRadios = document.querySelectorAll('input[type="radio"][data-arch]');
    const statusCheckboxes = document.querySelectorAll('.filter-checkbox[data-filter-type="status"]');
    const packageSearch = document.getElementById('package-search');
    const resetButton = document.getElementById('reset-filters');
    
    if (!packageSearch || !resetButton) {
      console.warn('Filter elements not found in DOM');
      return;
    }
    
    /**
     * Get architecture filter states
     * Returns {show: [archs], hide: [archs], only: [archs]}
     */
    function getArchStates() {
      const states = { show: [], hide: [], only: [] };
      
      // Get unique architectures
      const archs = new Set();
      archRadios.forEach(radio => archs.add(radio.getAttribute('data-arch')));
      
      archs.forEach(arch => {
        const checkedRadio = document.querySelector(`input[type="radio"][data-arch="${arch}"]:checked`);
        if (checkedRadio) {
          const mode = checkedRadio.getAttribute('data-mode');
          states[mode].push(arch);
        }
      });
      
      return states;
    }
    
    /**
     * Apply all active filters to the tables
     */
    function applyFilters() {
      const archStates = getArchStates();
      const selectedArchsShow = archStates.show;
      const selectedArchsOnly = archStates.only;
      // archStates.hide is implicit - any arch not in show or only is hidden
      
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
          
          // Check "only" architecture filter (FTBFS only on selected archs)
          if (isVisible && selectedArchsOnly.length > 0) {
            const buildCells = row.querySelectorAll('[data-arch]');
            const failedArchs = [];
            
            buildCells.forEach(cell => {
              const arch = cell.getAttribute('data-arch');
              const status = cell.getAttribute('data-status');
              // Check if this is a failure status
              if (status && status !== '') {
                failedArchs.push(arch);
              }
            });
            
            // Package should fail on ALL selected "only" archs
            // AND should NOT fail on any other arch
            const failsOnSelectedOnly = selectedArchsOnly.every(arch => failedArchs.includes(arch));
            const doesNotFailOnOthers = failedArchs.every(arch => selectedArchsOnly.includes(arch));
            
            isVisible = failsOnSelectedOnly && doesNotFailOnOthers && failedArchs.length > 0;
            
            // When arch-only filter is active, skip regular arch/status filters
            // (they would interfere with the "only on" logic)
          } else {
            // Regular filters (only when arch-only is NOT active)
            
            // Check architecture filter (show if ANY cell matches selected archs)
            // If no archs are in "show" mode, hide the row (empty filter = show nothing)
            if (isVisible && selectedArchsShow.length === 0) {
              const buildCells = row.querySelectorAll('[data-arch]');
              // If there are build cells but no arch is selected to show, hide
              if (buildCells.length > 0) {
                isVisible = false;
              }
            } else if (isVisible && selectedArchsShow.length > 0) {
              const buildCells = row.querySelectorAll('[data-arch]');
              let archMatch = false;
              
              buildCells.forEach(cell => {
                const arch = cell.getAttribute('data-arch');
                if (selectedArchsShow.includes(arch)) {
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
                  if (selectedArchsShow.length === 0 || selectedArchsShow.includes(arch)) {
                    statusMatch = true;
                  }
                }
              });
              
              isVisible = statusMatch;
            }
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
    
    // Function to update button states based on "Only" mode
    function updateButtonStates() {
      const archStates = getArchStates();
      const hasOnlyMode = archStates.only.length > 0;
      
      archRadios.forEach(radio => {
        const arch = radio.getAttribute('data-arch');
        const isCurrentArchOnly = archStates.only.includes(arch);
        
        // If any arch is in "Only" mode:
        // - Keep all buttons enabled ONLY for the arch in "Only" mode
        // - Disable ALL buttons for other architectures (can't have multiple "Only" at once)
        if (hasOnlyMode) {
          if (!isCurrentArchOnly) {
            radio.disabled = true;
            radio.parentElement.style.opacity = '0.5';
            radio.parentElement.style.cursor = 'not-allowed';
          } else {
            radio.disabled = false;
            radio.parentElement.style.opacity = '1';
            radio.parentElement.style.cursor = 'pointer';
          }
        } else {
          // No "Only" mode active, enable all buttons
          radio.disabled = false;
          radio.parentElement.style.opacity = '1';
          radio.parentElement.style.cursor = 'pointer';
        }
      });
    }
    
    // Add event listeners
    archRadios.forEach(radio => {
      radio.addEventListener('change', function() {
        updateButtonStates();
        applyFilters();
      });
    });
    
    statusCheckboxes.forEach(checkbox => {
      checkbox.addEventListener('change', applyFilters);
    });
    
    packageSearch.addEventListener('input', applyFilters);
    
    resetButton.addEventListener('click', function() {
      // Reset arch radios to "show"
      archRadios.forEach(radio => {
        if (radio.getAttribute('data-mode') === 'show') {
          radio.checked = true;
        }
      });
      
      // Check all status checkboxes
      statusCheckboxes.forEach(checkbox => {
        checkbox.checked = true;
      });
      
      packageSearch.value = '';
      updateButtonStates();
      applyFilters();
    });
    
    // Initial filtering on page load
    updateButtonStates();
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

