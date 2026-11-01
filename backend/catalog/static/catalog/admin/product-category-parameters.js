// Reload the product form when the category changes so the category's
// parameter list appears. Unsaved entries are confirmed before reloading.
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var select = document.getElementById("id_category");
    if (!select) return;
    var original = select.value;
    select.addEventListener("change", function () {
      if (!select.value || select.value === original) return;
      if (!window.confirm("Load the parameter list of this category? Unsaved changes on this page will be lost.")) {
        select.value = original;
        return;
      }
      var url = new URL(window.location.href);
      url.searchParams.set("category", select.value);
      window.location.assign(url.toString());
    });
  });
})();
