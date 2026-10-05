// Builds assets/avatar/avatar.bundle.js for the Android app.
// three and @pixiv/three-vrm come from desktop/node_modules, so run `npm install`
// in desktop/ first. Re-run this after changing desktop/renderer/avatar.js:
//
//   cd mobile/avatar_web && npm install && npm run build

import { build } from "esbuild";

await build({
  entryPoints: ["entry.js"],
  bundle: true,
  format: "iife", // a classic script: Android web views won't load ES modules from file://
  minify: true,
  target: "es2020",
  outfile: "../assets/avatar/avatar.bundle.js",
  logLevel: "info",
});
