// Small behaviours shared by every page. Kept in a file (not inline) so the Content Security
// Policy can forbid inline scripts entirely.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset && event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

document.addEventListener("change", (event) => {
  if (event.target.matches("select[data-autosubmit]")) event.target.form.submit();
});

// Log viewer: on first load, scroll the root-cause line into view (links to a line carry an #anchor).
document.addEventListener("DOMContentLoaded", () => {
  if (location.hash) return;
  const root = document.querySelector(".logline.root");
  if (root) root.scrollIntoView({ block: "center" });
});
