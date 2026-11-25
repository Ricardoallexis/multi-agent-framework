// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of opening a session in its app (Claude, Codex, Copilot).
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

export const HELP = {
  "open.claude": {
    "title": "Open in Claude",
    "hint": "Open this session in the Claude desktop app.",
    "more": [
      "This opens a new Windows console with «claude --desktop --resume» and the session ID; you must confirm first.",
      "For security, the console opens in your home folder, never in the session folder.",
      "Claude shows new activity only after you close and reopen the app.",
      "If launching is disabled (gui_allow_launch option) or you are not on Windows, copy and run the command yourself."
    ]
  },
  "open.codex": {
    "title": "Open in Codex",
    "hint": "Open this thread in the Codex app with a link.",
    "more": [
      "The button is a link to the installed Codex app; this page does not start a program.",
      "Codex may ask before opening the session.",
      "Codex blocks writes while the app has the thread open, but this viewer only reads, so it is not affected."
    ]
  },

  "open.copilot": {
    "title": "Open in Copilot",
    "hint": "Open this session in the Copilot app with a link.",
    "more": [
      "The button is a link to the installed Copilot app; this page does not start a program.",
      "Copilot may ask before opening the session.",
      "Copilot shows new session activity only after you close and reopen the app."
    ]
  },
  "open.copy": {
    "title": "Copy command",
    "hint": "Copy the command to run it yourself in a terminal.",
    "more": [
      "Use this when the viewer cannot open the app itself.",
      "Run it inside the folder shown next to the command, if one is shown."
    ]
  }
};
