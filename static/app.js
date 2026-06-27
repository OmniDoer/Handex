(function () {
  async function copyText(targetId, button) {
    var target = document.getElementById(targetId);
    if (!target) return;
    var text = "value" in target ? target.value : target.textContent;
    try {
      await navigator.clipboard.writeText(text);
      var old = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(function () {
        button.textContent = old;
      }, 1200);
    } catch (error) {
      target.focus();
      if ("select" in target) target.select();
      document.execCommand("copy");
    }
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".copy-target");
    if (!button) return;
    copyText(button.getAttribute("data-target"), button);
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }
})();
