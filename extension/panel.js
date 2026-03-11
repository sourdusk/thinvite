(function () {
  "use strict";

  // Replace at build/deploy time with the actual EBS URL.
  var EBS_BASE = "%%EBS_URL%%";
  var token = null;
  var userId = null;
  var channelId = null;

  // --- Helpers -------------------------------------------------------------

  function formatDuration(minutes, infix) {
    var mid = infix ? infix + " " : "";
    if (minutes >= 1440) {
      var days = Math.floor(minutes / 1440);
      return days + " " + mid + "day" + (days !== 1 ? "s" : "");
    } else if (minutes >= 60) {
      var hours = Math.floor(minutes / 60);
      return hours + " " + mid + "hour" + (hours !== 1 ? "s" : "");
    }
    return minutes + " " + mid + "minute" + (minutes !== 1 ? "s" : "");
  }

  // --- State management ---------------------------------------------------

  function showState(id) {
    document.querySelectorAll(".state").forEach(function (el) {
      el.classList.add("hidden");
    });
    document.getElementById(id).classList.remove("hidden");
  }

  // --- Theme --------------------------------------------------------------

  Twitch.ext.onContext(function (ctx) {
    document.body.className = ctx.theme === "dark" ? "dark" : "light";
  });

  // --- Auth ---------------------------------------------------------------

  Twitch.ext.onAuthorized(function (auth) {
    token = auth.token;
    channelId = auth.channelId;

    var viewer = Twitch.ext.viewer;
    if (!viewer.id) {
      // Viewer hasn't shared identity — prompt them
      showState("identity-required");
      return;
    }

    userId = viewer.id;
    registerPubSub(userId);
    checkStatus();
  });

  // --- Identity sharing ---------------------------------------------------

  document.getElementById("share-identity-btn").addEventListener("click", function () {
    Twitch.ext.actions.requestIdShare();
  });

  // --- PubSub listener ----------------------------------------------------

  var currentPubSubUserId = null;

  function registerPubSub(uid) {
    if (currentPubSubUserId) {
      Twitch.ext.unlisten("whisper-" + currentPubSubUserId, onWhisper);
    }
    currentPubSubUserId = uid;
    Twitch.ext.listen("whisper-" + uid, onWhisper);
  }

  function onWhisper(target, contentType, message) {
    try {
      var data = JSON.parse(message);
      if (data.type === "redemption_ready") {
        checkStatus();
      }
    } catch (e) { /* ignore parse errors */ }
  }

  // --- API calls ----------------------------------------------------------

  function apiFetch(method, path, body) {
    var opts = {
      method: method,
      headers: {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
      },
    };
    if (body) opts.body = JSON.stringify(body);
    return fetch(EBS_BASE + path, opts).then(function (r) {
      return r.json().then(function (data) {
        return { status: r.status, data: data };
      });
    });
  }

  function checkStatus() {
    showState("loading");
    apiFetch("GET", "/api/ext/status").then(function (res) {
      if (res.status === 403) {
        showState("identity-required");
        return;
      }
      if (res.status === 404) {
        showState("not-configured");
        return;
      }
      if (res.status === 429) {
        document.getElementById("error-text").textContent =
          "Too many requests. Please try again later.";
        showState("error");
        return;
      }
      var d = res.data;
      if (d.has_pending_redemption && d.follow_age_eligible) {
        showState("both-available");
      } else if (d.has_pending_redemption) {
        showState("pending");
      } else if (d.on_cooldown) {
        document.getElementById("cooldown-text").textContent =
          "You claimed an invite recently. Check back later.";
        showState("cooldown");
      } else if (d.follow_age_eligible) {
        document.getElementById("eligible-text").textContent =
          "You\u2019ve followed for " + formatDuration(d.follow_age_minutes) +
          " \u2014 claim your Discord invite!";
        showState("eligible");
      } else if (d.follow_age_enabled && typeof d.follow_age_minutes === "number") {
        var needed = (d.min_follow_minutes || 0) - d.follow_age_minutes;
        var txt;
        if (needed > 0) {
          txt = "Follow for " + formatDuration(needed, "more");
          if (d.cp_enabled) txt += " or redeem channel points";
          txt += " to earn a Discord invite.";
        } else {
          txt = "Follow this channel";
          if (d.cp_enabled) txt += " or redeem channel points";
          txt += " to earn a Discord invite.";
        }
        document.getElementById("not-eligible-text").textContent = txt;
        showState("not-eligible");
      } else if (d.follow_age_enabled) {
        var txt = "Follow this channel";
        if (d.cp_enabled) txt += " or redeem channel points";
        txt += " to earn a Discord invite.";
        document.getElementById("not-eligible-text").textContent = txt;
        showState("not-eligible");
      } else if (d.cp_enabled) {
        document.getElementById("not-eligible-text").textContent =
          "Redeem channel points to earn a Discord invite.";
        showState("not-eligible");
      } else {
        showState("not-configured");
      }
    }).catch(function () {
      document.getElementById("error-text").textContent =
        "Could not load status. Please try again.";
      showState("error");
    });
  }

  function claim(type) {
    showState("loading");
    apiFetch("POST", "/api/ext/claim", { type: type }).then(function (res) {
      if (res.status === 200 && res.data.invite_url) {
        var link = document.getElementById("invite-link");
        link.href = res.data.invite_url;
        showState("success");
      } else if (res.status === 429) {
        document.getElementById("error-text").textContent =
          "Too many requests. Please try again later.";
        showState("error");
      } else {
        var msg = res.data.error === "on_cooldown"
          ? "You already claimed an invite recently."
          : res.data.error === "not_eligible"
            ? "You are not eligible yet."
            : "Something went wrong. Please try again.";
        document.getElementById("error-text").textContent = msg;
        showState("error");
      }
    }).catch(function () {
      document.getElementById("error-text").textContent =
        "Network error. Please try again.";
      showState("error");
    });
  }

  // --- Button handlers ----------------------------------------------------

  document.getElementById("claim-follow-btn").addEventListener("click", function () { claim("follow_age"); });
  document.getElementById("claim-redeem-btn").addEventListener("click", function () { claim("redemption"); });
  document.getElementById("claim-follow-btn-2").addEventListener("click", function () { claim("follow_age"); });
  document.getElementById("claim-redeem-btn-2").addEventListener("click", function () { claim("redemption"); });
  document.getElementById("retry-btn").addEventListener("click", checkStatus);
})();
