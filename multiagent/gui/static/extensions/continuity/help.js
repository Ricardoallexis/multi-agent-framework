// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the continuity screens (CNT-T06). Spanish copies live in the extension dictionary.

export const HELP = {
  "continuity.view": {
    "title": "Continuity decisions",
    "hint": "Tasks waiting because an app reached its usage limit.",
    "more": [
      "Each card shows which app reached its limit, when it resets, and where that time came from.",
      "Nothing happens on its own: each choice asks you to confirm before it is saved.",
      "When the time arrives, the task resumes on its own unless you asked to hand it off."
    ]
  },
  "continuity.slot": {
    "title": "Continuity for this session",
    "hint": "Shows whether this session's task is waiting because of a usage limit.",
    "more": [
      "If the session is not waiting, there is nothing to decide here.",
      "If it is waiting, you will see the same decision card as on the decisions screen."
    ]
  },
  "continuity.usage": {
    "title": "Continuity policy",
    "hint": "How the framework responds when an app reaches its limit.",
    "more": [
      "Shows the mode, how long to wait before suggesting another agent, and how many hand-offs are allowed.",
      "Below the values you can change the mode, the wait threshold and the apps that may take over.",
      "A task is never handed off during an integration."
    ]
  },
  "continuity.wait": {
    "title": "Wait until the shown time",
    "hint": "Choose to wait until the limit resets.",
    "more": [
      "You must confirm before your choice is saved.",
      "The task stays paused and resumes on its own shortly after the shown time.",
      "The time comes from the app or, if the app did not provide one, from a configured pause."
    ]
  },
  "continuity.delegate": {
    "title": "Hand off to another agent",
    "hint": "Ask another agent to take the task instead of waiting.",
    "more": [
      "A confirmation tells you who would receive the task and what would be sent.",
      "The task envelope and saved patch would be sent, never the conversation.",
      "For now, only your choice is saved: the task will not resume on its own until it is handed off or you choose to wait."
    ]
  },
  "continuity.later": {
    "title": "Decide later",
    "hint": "Close the choices without changing anything.",
    "more": [
      "The task keeps waiting, and your decision stays pending.",
      "You can reopen the choices whenever you want."
    ]
  },
  "continuity.refresh": {
    "title": "Refresh",
    "hint": "Read the waiting tasks again.",
    "more": [
      "The list also refreshes on its own from time to time.",
      "Refreshing does not change any task."
    ]
  }
};
