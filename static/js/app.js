document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('table.datatable').forEach(function (table) {
    if (window.DataTable) {
      // DataTables requires a consistent cell count; empty states are rendered by its language.emptyTable option, not a colspan row in tbody.
      new DataTable(table, { paging: true, pageLength: 10, lengthChange: false, searching: false,
        ordering: true, info: true, order: [], language: { emptyTable: 'No cases found.' } });
    }
  });
  const dateInput = document.querySelector('input[type="date"]');
  if (dateInput && !dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);
});
