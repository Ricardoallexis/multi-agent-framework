// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
export const HELP = {
  "task_waiting.view": {
    "title": "Waiting for your decision",
    "hint": "Task questions that need your answer, plus other waits.",
    "more": [
      "Each card shows the question, who it is waiting for, when it started, and its reference.",
      "This view only reads data; it does not approve or resolve any wait."
    ]
  },
  "task_waiting.panel": {
    "title": "Waiting questions",
    "hint": "Tasks across the system that need your decision, no matter which session is open.",
    "more": [
      "The wait badge is separate from the task's run state.",
      "Viewing this panel does not answer the question or authorize a run."
    ]
  },
  "task_waiting.refresh": {
    "title": "Refresh waits",
    "hint": "Check for waits again without changing tasks.",
    "more": [
      "The list updates regularly while the tab is visible.",
      "If a request fails, the previous data stays visible with a notice."
    ]
  },
  "task_waiting.others": {
    "title": "Other waits",
    "hint": "Tasks waiting for an agent, another task, a quota, or an integration window.",
    "more": [
      "These waits are not questions for you.",
      "The card keeps the task state and shows who or what it is waiting for."
    ]
  }
};
