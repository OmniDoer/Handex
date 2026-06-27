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

  function updateJob(article, job, source) {
    var status = article.querySelector("[data-job-status]");
    var stdout = article.querySelector("[data-job-stdout]");
    var stderr = article.querySelector("[data-job-stderr]");
    var stop = article.querySelector("[data-job-stop]");
    var statusText = (job.status || "").toUpperCase();
    if (job.exit_code !== null && job.exit_code !== undefined) {
      statusText += " / " + job.exit_code;
    }
    if (status) status.textContent = statusText || "-";
    if (stdout && stdout.value !== (job.stdout || "")) stdout.value = job.stdout || "";
    if (stderr && stderr.value !== (job.stderr || "")) stderr.value = job.stderr || "";
    if (["completed", "failed", "stopped", "lost"].indexOf(job.status) !== -1) {
      if (stop) stop.remove();
      if (source) source.close();
      article.removeAttribute("data-job-events");
    }
  }

  function connectJobStreams() {
    if (!("EventSource" in window)) return;
    document.querySelectorAll("[data-job-events]").forEach(function (article) {
      if (article.dataset.jobStreamAttached === "1") return;
      article.dataset.jobStreamAttached = "1";
      var source = new EventSource(article.getAttribute("data-job-events"));
      source.addEventListener("job", function (event) {
        try {
          updateJob(article, JSON.parse(event.data), source);
        } catch (error) {
          source.close();
        }
      });
      source.onerror = function () {
        source.close();
        delete article.dataset.jobStreamAttached;
      };
    });
  }

  connectJobStreams();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }
})();
