// Buddy's avatar for the Android app's web view.
//
// Reuses the desktop renderer's Avatar class (VRM loading, framing, idle motion,
// blinking, mouth movement, moods, eye contact), so both apps animate the same
// way. With no mouse pointer on a phone, the avatar looks at you. esbuild bundles
// this file with three.js and three-vrm into assets/avatar/avatar.bundle.js.
//
// Flutter drives it through window.buddyAvatar:
//   setAvatarUrl(url)  where to load the .vrm from (Buddy's backend serves it)
//   setState(state)    "idle" | "listening" | "thinking" | "speaking"
//   setMood(mood)      "neutral" | "happy" | "sad" | "angry" | "surprised" | "relaxed"

import { Avatar } from "../../desktop/renderer/avatar.js";

let resolveUrl;
const avatarUrl = new Promise((resolve) => (resolveUrl = resolve));

// Avatar.load() asks window.buddy for the model URL (the desktop app's preload API).
window.buddy = { getAvatarUrl: () => avatarUrl };

// A phone screen is tall and narrow: fit more of the model than the desktop's 0.6.
// "?fraction=" overrides it, which is handy when trying other models.
const fraction = Number(new URLSearchParams(location.search).get("fraction")) || 0.9;
const avatar = new Avatar(document.getElementById("avatar"), { visibleFraction: fraction });

window.buddyAvatar = {
  setAvatarUrl: (url) => resolveUrl(url || null),
  setState: (state) => avatar.setState(state),
  setMood: (mood) => avatar.setMood(mood),
};
