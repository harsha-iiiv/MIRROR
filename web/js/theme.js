// Apply the saved or preferred colour theme before the first paint.
(function () {
  var t = null;
  try { t = localStorage.getItem('mirror-theme'); } catch (e) { /* storage disabled */ }
  if (!t) t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
})();
