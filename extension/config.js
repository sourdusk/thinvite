(function () {
  "use strict";

  var EBS_BASE = "%%EBS_URL%%";
  var token = null;

  Twitch.ext.onAuthorized(function (auth) {
    token = auth.token;
    // Load current config from Twitch Configuration Service
    var config = Twitch.ext.configuration.broadcaster;
    if (config && config.content) {
      try {
        var c = JSON.parse(config.content);
        document.getElementById("min-follow").value = c.min_follow_days || 0;
        document.getElementById("cooldown").value = c.cooldown_days || 30;
      } catch (e) { /* use defaults */ }
    }
  });

  document.getElementById("save-btn").addEventListener("click", function () {
    var minFollow = parseInt(document.getElementById("min-follow").value, 10);
    var cooldown = parseInt(document.getElementById("cooldown").value, 10);
    if (isNaN(minFollow) || minFollow < 0) { msg("Min follow must be >= 0", true); return; }
    if (isNaN(cooldown) || cooldown < 1) { msg("Cooldown must be >= 1 day", true); return; }

    fetch(EBS_BASE + "/api/ext/config", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ min_follow_days: minFollow, cooldown_days: cooldown }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.ok) {
        // Also save to Twitch Configuration Service for panel cache
        Twitch.ext.configuration.set("broadcaster", "1",
          JSON.stringify({ min_follow_days: minFollow, cooldown_days: cooldown }));
        msg("Saved!", false);
      } else {
        msg("Error: " + (data.error || "unknown"), true);
      }
    }).catch(function () {
      msg("Network error", true);
    });
  });

  function msg(text, isError) {
    var el = document.getElementById("status-msg");
    el.textContent = text;
    el.style.color = isError ? "#f06060" : "#4ecdc4";
  }
})();
