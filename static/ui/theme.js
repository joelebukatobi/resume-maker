// Loaded blocking in <head> so the first paint is already the right theme.
// The choice lives in this browser only: dark, light or system.
(function () {
  var mode = "dark";
  try {
    var stored = localStorage.getItem("rm-theme");
    if (stored === "light" || stored === "dark" || stored === "system") mode = stored;
  } catch (e) { /* storage blocked: keep the default */ }
  document.documentElement.dataset.theme = mode;
})();
