(function () {
  "use strict";

  var EBS_BASE = "%%EBS_URL%%";
  var token = null;

  var UNIT_MULTIPLIERS = { minutes: 1, hours: 60, days: 1440 };

  function minutesToDisplay(minutes) {
    if (minutes >= 1440 && minutes % 1440 === 0) return { val: minutes / 1440, unit: "days" };
    if (minutes >= 60 && minutes % 60 === 0) return { val: minutes / 60, unit: "hours" };
    return { val: minutes, unit: "minutes" };
  }

  Twitch.ext.onAuthorized(function (auth) {
    token = auth.token;
    // Load current config from EBS
    fetch(EBS_BASE + "/api/ext/config", {
      method: "GET",
      headers: { "Authorization": "Bearer " + token },
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.min_follow_minutes != null) {
        var d = minutesToDisplay(data.min_follow_minutes);
        document.getElementById("min-follow").value = d.val;
        document.getElementById("min-follow-unit").value = d.unit;
      }
      if (data.cooldown_days != null) {
        document.getElementById("cooldown").value = data.cooldown_days;
      }
    }).catch(function () { /* use defaults */ });
  });

  document.getElementById("save-btn").addEventListener("click", function () {
    var minFollow = parseInt(document.getElementById("min-follow").value, 10);
    var unit = document.getElementById("min-follow-unit").value;
    var cooldown = parseInt(document.getElementById("cooldown").value, 10);
    if (isNaN(minFollow) || minFollow < 0) { msg("Min follow must be >= 0", true); return; }
    if (isNaN(cooldown) || cooldown < 1) { msg("Cooldown must be >= 1 day", true); return; }

    var minFollowMinutes = minFollow * (UNIT_MULTIPLIERS[unit] || 1);

    fetch(EBS_BASE + "/api/ext/config", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ min_follow_minutes: minFollowMinutes, cooldown_days: cooldown }),
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (data) {
          msg("Error: " + (data.error || r.status), true);
        });
      }
      return r.json().then(function (data) {
        if (data.ok) {
          msg("Saved!", false);
        } else {
          msg("Error: " + (data.error || "unknown"), true);
        }
      });
    }).catch(function (err) {
      msg("Network error: " + (err.message || "check console"), true);
    });
  });

  function msg(text, isError) {
    var el = document.getElementById("status-msg");
    el.textContent = text;
    el.style.color = isError ? "#f06060" : "#4ecdc4";
  }
})();
