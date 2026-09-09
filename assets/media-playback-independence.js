(function () {
  "use strict";

  if (window.__adtIndependentMediaInstalled) return;
  window.__adtIndependentMediaInstalled = true;

  var mediaPrototype = window.HTMLMediaElement && window.HTMLMediaElement.prototype;
  if (!mediaPrototype) return;

  var nativePlay = mediaPrototype.play;
  var nativePause = mediaPrototype.pause;
  var narrationActivationUntil = 0;
  var explicitVideoCloseUntil = 0;
  var observedNarrationAudios = new WeakSet();

  function now() {
    return window.performance && typeof window.performance.now === "function"
      ? window.performance.now()
      : Date.now();
  }

  function mediaSource(media) {
    return String(media.currentSrc || media.getAttribute("src") || media.src || "");
  }

  function isNarrationAudio(media) {
    return media instanceof window.HTMLAudioElement &&
      /\/content\/i18n\/[^/]+\/audio\//i.test(mediaSource(media));
  }

  function isSignLanguageVideo(media) {
    if (!(media instanceof window.HTMLVideoElement)) return false;
    if (/\/content\/i18n\/[^/]+\/video\//i.test(mediaSource(media))) return true;
    return Boolean(
      media.closest("[data-sign-language-player]") ||
      media.parentElement && media.parentElement.querySelector('[aria-label="Drag sign language video"]')
    );
  }

  function isSignLanguageCloseButton(target) {
    if (!(target instanceof window.Element)) return false;
    var button = target.closest("button");
    if (!button) return false;
    var player = button.closest("[data-sign-language-player]") || button.parentElement;
    var isDragHandle = button.matches('[aria-label="Drag sign language video"]') ||
      button.hasAttribute("data-sign-language-drag-handle");
    return Boolean(player && player.querySelector && player.querySelector("video") && !isDragHandle);
  }

  function setPlaybackState(kind, state) {
    document.documentElement.setAttribute("data-" + kind + "-playback", state);
  }

  function observeNarrationAudio(audio) {
    if (observedNarrationAudios.has(audio)) return;
    observedNarrationAudios.add(audio);
    audio.addEventListener("play", function () { setPlaybackState("read-aloud", "playing"); });
    audio.addEventListener("pause", function () { setPlaybackState("read-aloud", "paused"); });
    audio.addEventListener("ended", function () { setPlaybackState("read-aloud", "ended"); });
    audio.addEventListener("error", function () { setPlaybackState("read-aloud", "error"); });
  }

  mediaPrototype.play = function () {
    if (isNarrationAudio(this)) {
      observeNarrationAudio(this);
      setPlaybackState("read-aloud", "starting");
      narrationActivationUntil = now() + 2000;
    }
    return nativePlay.apply(this, arguments);
  };

  mediaPrototype.pause = function () {
    if (
      isSignLanguageVideo(this) &&
      now() < narrationActivationUntil &&
      now() >= explicitVideoCloseUntil
    ) {
      return;
    }
    return nativePause.apply(this, arguments);
  };

  function allowExplicitVideoClose(event) {
    if (isSignLanguageCloseButton(event.target)) explicitVideoCloseUntil = now() + 2000;
  }

  window.addEventListener("pointerdown", allowExplicitVideoClose, true);
  window.addEventListener("click", allowExplicitVideoClose, true);
  window.addEventListener("keydown", function (event) {
    if ((event.key === "Enter" || event.key === " ") && isSignLanguageCloseButton(event.target)) {
      explicitVideoCloseUntil = now() + 2000;
    }
  }, true);

  window.addEventListener("play", function (event) {
    if (!isSignLanguageVideo(event.target)) return;
    event.target.setAttribute("data-independent-media-playback", "");
    setPlaybackState("sign-language", "playing");
    event.stopPropagation();
  }, true);

  window.addEventListener("pause", function (event) {
    if (isSignLanguageVideo(event.target)) setPlaybackState("sign-language", "paused");
  }, true);
  window.addEventListener("ended", function (event) {
    if (isSignLanguageVideo(event.target)) setPlaybackState("sign-language", "ended");
  }, true);

  document.documentElement.setAttribute("data-independent-media-playback", "");
})();
