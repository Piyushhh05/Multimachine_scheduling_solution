document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector("button[type='submit']");
      if (!button) return;
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Scheduling…';
    });
  });
});
