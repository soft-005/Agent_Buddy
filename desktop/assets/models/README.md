# Avatar models

Put VRM avatars in this folder. Any file name ending in `.vrm` works. Switch between them from the tray icon: right-click, then **Avatar**. That menu also has **Add avatar…**, which copies a `.vrm` from anywhere into this folder, and **Open avatars folder**.

With **Automatic** chosen, `avatar.vrm` wins, otherwise the first file alphabetically is used. The phone app uses the same automatic choice unless you pick another avatar in its Settings. To use a file somewhere else, set the `BUDDY_AVATAR` environment variable to its path (the tray choice still wins).

Until a model exists, or if it fails to load, the app shows a placeholder sphere. The reason is printed in the developer console (Ctrl+Shift+I).

Make one for free in VRoid Studio and export it as VRM. You can also download a model from VRoid Hub, but check its license first. Models with the VRM expressions happy, sad, angry, surprised and relaxed show Buddy's moods on their face. Every model also shows them through head and eyelid movement. Models with eye bones follow the mouse pointer with their eyes as well as their head.
