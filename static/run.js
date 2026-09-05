(function () {
  var logEl = document.getElementById("log");
  var startBtn = document.getElementById("start-btn");

  function connect(since) {
    var src = new EventSource("/run/stream?since=" + since);
    src.onmessage = function (ev) {
      logEl.textContent += (logEl.textContent ? "\n" : "") + ev.data;
      logEl.scrollTop = logEl.scrollHeight;
    };
    src.addEventListener("done", function () {
      src.close();
      window.location.reload();
    });
    src.onerror = function () {
      src.close();
    };
  }

  if (window.INITIAL_RUNNING) {
    connect(window.INITIAL_LOG_COUNT);
  }
})();
